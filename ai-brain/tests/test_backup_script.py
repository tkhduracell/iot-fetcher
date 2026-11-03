"""backup-memory.sh, run for real against a stand-in docker and GCS client.

The script is plain bash and the parts that can go wrong are in how it calls tar
and docker, so it is exercised end to end, with nothing mocked but the two ends
that need the network: a fake ``docker`` on PATH runs the uploader locally, with a
fake ``google.cloud.storage`` that writes to a directory.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup-memory.sh"

FAKE_STORAGE = """
import json, os, shutil
from pathlib import Path

class _Blob:
    def __init__(self, bucket, name):
        self.bucket, self.name = bucket, name

    def upload_from_filename(self, path, content_type=None, if_generation_match=None):
        if os.environ.get("FAKE_GCS_FAIL"):
            raise RuntimeError("gcs is down")
        out = Path(os.environ["FAKE_GCS_DIR"]) / self.bucket / self.name
        out.parent.mkdir(parents=True, exist_ok=True)
        assert if_generation_match == 0
        assert content_type == "application/gzip"
        shutil.copyfile(path, out)

class _Bucket:
    def __init__(self, name):
        self.name = name
    def blob(self, name):
        return _Blob(self.name, name)

class Client:
    @classmethod
    def from_service_account_info(cls, info):
        assert info == {"type": "service_account"}
        return cls()
    def bucket(self, name):
        return _Bucket(name)
"""

# Stands in for a running iot-fetcher. Only the two calls the script makes.
FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
    inspect)
        [ "$2" = "iot-fetcher" ] || exit 1
        ;;
    exec)
        shift 3   # exec -i <container>
        [ "$1" = "python" ] || exit 9
        shift
        exec "$FAKE_PYTHON" "$@"
        ;;
    *) exit 9 ;;
esac
"""


@pytest.fixture
def box(tmp_path: Path) -> dict:
    memory = tmp_path / "memory"
    (memory / "brain" / "facts").mkdir(parents=True)
    (memory / "brain" / "sessions").mkdir()
    (memory / "experts" / "energy" / "sessions").mkdir(parents=True)
    (memory / "_repo" / "abc").mkdir(parents=True)
    (memory / "constitution.md").write_text("be useful")
    (memory / "brain" / "facts" / "pool.md").write_text("the pool")
    (memory / "brain" / "facts" / "half.md.tmp").write_text("half a write")
    (memory / "brain" / "sessions" / "20270101-000000-aaaa.jsonl.gz").write_text("log")
    (memory / "experts" / "energy" / "sessions" / "20270101-000000-bbbb.jsonl").write_text("log")
    (memory / "_repo" / "abc" / "README.md").write_text("the repo")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(FAKE_DOCKER)
    docker.chmod(docker.stat().st_mode | stat.S_IEXEC)

    fake = tmp_path / "fake" / "google" / "cloud"
    fake.mkdir(parents=True)
    (fake.parent / "__init__.py").write_text("")
    (fake / "__init__.py").write_text("")
    (fake / "storage.py").write_text(FAKE_STORAGE)

    gcs = tmp_path / "gcs"
    return {
        "memory": memory,
        "gcs": gcs,
        "env": {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "MEMORY_DIR": str(memory),
            "ENV_FILE": str(tmp_path / "no-such.env"),
            "FAKE_PYTHON": sys.executable,
            "FAKE_GCS_DIR": str(gcs),
            "PYTHONPATH": str(tmp_path / "fake"),
            "GOOGLE_SERVICE_ACCOUNT": "'" + json.dumps({"type": "service_account"}) + "'",
        },
    }


def run(box: dict, *args: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env={**box["env"], **env},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _uploaded(box: dict) -> list[Path]:
    return sorted(p for p in box["gcs"].rglob("*") if p.is_file()) if box["gcs"].exists() else []


def test_script_is_executable():
    assert SCRIPT.stat().st_mode & stat.S_IXUSR


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck not installed")
def test_shellcheck_is_clean():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def test_dry_run_builds_and_checks_but_uploads_nothing(box):
    done = run(box, "--dry-run")
    assert done.returncode == 0, done.stderr
    assert "nothing uploaded" in done.stdout
    assert _uploaded(box) == []


def test_upload_lands_under_the_templated_prefix(box):
    done = run(box, AI_BRAIN_BACKUP_URI="gcs://my-bucket/ai-brain/%Y/")
    assert done.returncode == 0, done.stderr

    files = _uploaded(box)
    assert len(files) == 1
    parts = files[0].relative_to(box["gcs"]).parts
    assert parts[0] == "my-bucket"
    assert parts[1] == "ai-brain"
    assert parts[2].isdigit() and len(parts[2]) == 4
    assert parts[3].startswith("ai-brain-memory-") and parts[3].endswith(".tar.gz")


def test_archive_has_the_memory_and_not_what_comes_back_on_its_own(box):
    assert run(box, AI_BRAIN_BACKUP_URI="gcs://b/p/").returncode == 0
    with tarfile.open(_uploaded(box)[0]) as tar:
        names = set(tar.getnames())
    assert "./constitution.md" in names
    assert "./brain/facts/pool.md" in names
    assert not [n for n in names if n.endswith(".tmp")]
    assert not [n for n in names if "_repo" in n]
    assert not [n for n in names if "sessions" in n]


def test_sessions_can_be_opted_in(box):
    assert run(box, AI_BRAIN_BACKUP_URI="gcs://b/p/", BACKUP_SESSIONS="1").returncode == 0
    with tarfile.open(_uploaded(box)[0]) as tar:
        names = set(tar.getnames())
    assert "./brain/sessions/20270101-000000-aaaa.jsonl.gz" in names


def test_uri_is_read_from_the_env_file(box, tmp_path):
    env_file = tmp_path / "ai-brain.env"
    env_file.write_text('OTHER=1\nAI_BRAIN_BACKUP_URI="gcs://from-file/p/"\n')
    assert run(box, ENV_FILE=str(env_file)).returncode == 0
    assert _uploaded(box)[0].relative_to(box["gcs"]).parts[0] == "from-file"


def test_missing_or_wrong_uri_fails_before_anything_else(box):
    done = run(box)
    assert done.returncode == 1
    assert "AI_BRAIN_BACKUP_URI" in done.stderr
    assert run(box, AI_BRAIN_BACKUP_URI="s3://nope/").returncode == 1


def test_a_directory_that_is_not_the_memory_volume_is_refused(box, tmp_path):
    """An unmounted volume is an empty directory, and it must not become a snapshot."""
    empty = tmp_path / "empty"
    empty.mkdir()
    done = run(box, MEMORY_DIR=str(empty), AI_BRAIN_BACKUP_URI="gcs://b/p/")
    assert done.returncode == 1
    assert "constitution.md" in done.stderr
    assert _uploaded(box) == []


def test_missing_container_is_a_clear_error(box):
    done = run(box, AI_BRAIN_BACKUP_URI="gcs://b/p/", FETCHER_CONTAINER="not-there")
    assert done.returncode == 1
    assert "not-there" in done.stderr


def test_upload_failure_fails_the_script(box):
    done = run(box, AI_BRAIN_BACKUP_URI="gcs://b/p/", FAKE_GCS_FAIL="1")
    assert done.returncode != 0
    assert "gcs is down" in done.stderr
    assert "uploaded" not in done.stdout


def test_short_read_is_never_published(box):
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPT.parent / "backup_upload.py"),
            "gcs://b/p/",
            "x.tar.gz",
            "1000",
        ],
        input=b"only a few bytes",
        env=box["env"],
        capture_output=True,
        check=False,
    )
    assert done.returncode != 0
    assert b"expected 1000 bytes" in done.stderr
    assert _uploaded(box) == []


def test_unknown_argument_is_refused(box):
    assert run(box, "--nope").returncode == 2


def test_temp_files_are_cleaned_up(box, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    assert run(box, "--dry-run", TMPDIR=str(scratch)).returncode == 0
    assert list(scratch.iterdir()) == []
