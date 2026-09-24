import io
import tarfile
from pathlib import Path

import httpx
import pytest
import respx

from ai_brain.repo import RepoSnapshot

SLUG = "example/fake-repo"
REF = "main"
SHA = "abc123def456"


def _tar_bytes(entries: dict[str, bytes], top: str = f"fake-repo-{SHA}") -> bytes:
    """Build a gzipped tar in memory, shaped like codeload's own export: every
    entry under one top-level "<repo>-<sha>/" directory. ``entries`` maps a
    repo-relative path to its file content."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in entries.items():
            info = tarfile.TarInfo(name=f"{top}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _mock_commit(sha: str = SHA):
    respx.get(f"https://api.github.com/repos/{SLUG}/commits/{REF}").mock(
        return_value=httpx.Response(200, json={"sha": sha})
    )


def _mock_commit_log(shas: list[str]):
    return respx.get(f"https://api.github.com/repos/{SLUG}/commits").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "sha": sha,
                    "commit": {
                        "message": f"subject for {sha}\n\nbody",
                        "author": {"date": "2026-09-20T10:00:00Z"},
                    },
                }
                for sha in shas
            ],
        )
    )


def _mock_tarball(entries: dict[str, bytes], sha: str = SHA):
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{sha}").mock(
        return_value=httpx.Response(
            200, content=_tar_bytes(entries, top=f"fake-repo-{sha}")
        )
    )


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


@respx.mock
async def test_refresh_extracts_tracked_files(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"README.md": b"# fake repo\n", "pkg/main.go": b"package main\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert state.sha == SHA
    assert (state.root / "README.md").read_text() == "# fake repo\n"
    assert (state.root / "pkg" / "main.go").read_text() == "package main\n"


@respx.mock
async def test_refresh_swaps_current_pointer(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"a.md": b"hi\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()

    current = tmp_path / "_repo" / "current"
    assert current.is_symlink()
    assert (current / "a.md").read_text() == "hi\n"


@respx.mock
async def test_refresh_skips_download_when_sha_unchanged(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    tar_route = respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=_tar_bytes({"a.md": b"hi\n"}))
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()
    await snap.refresh()

    assert tar_route.call_count == 1


@respx.mock
async def test_refresh_keeps_keep_snapshots_worth_of_history(tmp_path, http):
    """KEEP_SNAPSHOTS (2) is current plus one spare -- a third refresh must
    push the oldest one out, but the second refresh's SHA survives."""
    from ai_brain.repo import KEEP_SNAPSHOTS

    assert KEEP_SNAPSHOTS == 2, "this test's shas assume the documented default"

    async def refresh_as(sha: str) -> None:
        respx.get(f"https://api.github.com/repos/{SLUG}/commits/{REF}").mock(
            return_value=httpx.Response(200, json={"sha": sha})
        )
        _mock_tarball({"a.md": sha.encode()}, sha=sha)
        _mock_commit_log([sha])
        await snap.refresh()

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await refresh_as("sha1")
    await refresh_as("sha2")

    repo_dir = tmp_path / "_repo"
    remaining = {p.name for p in repo_dir.iterdir() if p.is_dir() or p.is_symlink()}
    assert remaining == {"current", "sha1", "sha2"}, "one spare survives a second refresh"

    await refresh_as("sha3")
    remaining = {p.name for p in repo_dir.iterdir() if p.is_dir() or p.is_symlink()}
    assert "sha1" not in remaining, "a third refresh pushes the oldest spare out"
    assert remaining == {"current", "sha2", "sha3"}


@respx.mock
async def test_commits_are_cached_newest_first(tmp_path, http):
    _mock_commit()
    _mock_commit_log(["c3", "c2", "c1"])
    _mock_tarball({"a.md": b"x\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()

    commits = snap.commits(2)
    assert [c.sha for c in commits] == ["c3", "c2"]
    assert commits[0].subject == "subject for c3"


@respx.mock
async def test_commit_log_is_queried_at_the_resolved_sha_not_the_moving_ref(tmp_path, http):
    """REPO_REF (e.g. "main") keeps moving; the log must match the SHA that
    was actually extracted, not whatever main points to by the time the log
    call happens."""
    _mock_commit()  # resolves REF -> SHA
    commit_log_route = respx.get(f"https://api.github.com/repos/{SLUG}/commits").mock(
        return_value=httpx.Response(200, json=[])
    )
    _mock_tarball({"a.md": b"x\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()

    assert commit_log_route.call_count == 1
    request = commit_log_route.calls[0].request
    assert request.url.params["sha"] == SHA


@respx.mock
async def test_commit_log_is_not_refetched_when_the_sha_is_unchanged_and_already_cached(
    tmp_path, http
):
    _mock_commit()
    commit_log_route = respx.get(f"https://api.github.com/repos/{SLUG}/commits").mock(
        return_value=httpx.Response(200, json=[{"sha": SHA, "commit": {}}])
    )
    _mock_tarball({"a.md": b"x\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()
    assert commit_log_route.call_count == 1

    await snap.refresh()  # SHA unchanged, and _commits is already populated
    assert commit_log_route.call_count == 1


@respx.mock
async def test_commit_log_is_backfilled_once_for_an_adopted_snapshot(tmp_path, http):
    """adopt_existing() makes no network call, so _commits starts empty even
    though state is not None -- the next refresh() must still fetch the log
    exactly once, not skip it just because the SHA looks unchanged."""
    _mock_commit()
    commit_log_route = _mock_commit_log([SHA])
    _mock_tarball({"a.md": b"x\n"})

    first = RepoSnapshot(tmp_path, SLUG, REF, http)
    await first.refresh()
    assert commit_log_route.call_count == 1

    second = RepoSnapshot(tmp_path, SLUG, REF, http)
    second.adopt_existing()
    assert second.commits(10) == []  # nothing fetched yet for this instance

    await second.refresh()  # unchanged SHA, but this instance's _commits was empty

    assert commit_log_route.call_count == 2
    assert len(second.commits(10)) == 1


@respx.mock
async def test_a_failed_refresh_raises_and_leaves_no_partial_dir(tmp_path, http):
    respx.get(f"https://api.github.com/repos/{SLUG}/commits/{REF}").mock(
        return_value=httpx.Response(200, json={"sha": SHA})
    )
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(500)
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    with pytest.raises(httpx.HTTPStatusError):
        await snap.refresh()

    assert snap.state is None
    assert not (tmp_path / "_repo" / SHA).exists()


# -- tar safety ----------------------------------------------------------


def _malicious_tar_bytes(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for info, content in members:
            if content is not None:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
            else:
                tar.addfile(info)
    return buf.getvalue()


@respx.mock
async def test_extraction_refuses_a_traversal_member(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    top = f"fake-repo-{SHA}"
    evil = tarfile.TarInfo(name=f"{top}/../../etc/passwd")
    safe = tarfile.TarInfo(name=f"{top}/README.md")
    tar_bytes = _malicious_tar_bytes([(evil, b"pwned\n"), (safe, b"fine\n")])
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=tar_bytes)
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (tmp_path / "etc" / "passwd").exists()
    assert not (tmp_path.parent / "etc" / "passwd").exists()
    assert (state.root / "README.md").read_text() == "fine\n"


@respx.mock
async def test_extraction_refuses_an_absolute_path_member(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    top = f"fake-repo-{SHA}"
    evil = tarfile.TarInfo(name="/etc/passwd")
    safe = tarfile.TarInfo(name=f"{top}/README.md")
    tar_bytes = _malicious_tar_bytes([(evil, b"pwned\n"), (safe, b"fine\n")])
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=tar_bytes)
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    # An absolute-path member is refused outright, so only the safe file lands.
    assert list(state.root.iterdir()) == [state.root / "README.md"]
    assert (state.root / "README.md").read_text() == "fine\n"


@respx.mock
async def test_extraction_refuses_a_symlink_member(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    top = f"fake-repo-{SHA}"
    link = tarfile.TarInfo(name=f"{top}/escape")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    safe = tarfile.TarInfo(name=f"{top}/README.md")
    tar_bytes = _malicious_tar_bytes([(link, None), (safe, b"fine\n")])
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=tar_bytes)
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (state.root / "escape").exists()
    assert (state.root / "README.md").read_text() == "fine\n"


@respx.mock
async def test_extraction_refuses_oversized_tarball(tmp_path, http, monkeypatch):
    """The compressed-download cap in _download_and_extract."""
    from ai_brain import repo as repo_module

    monkeypatch.setattr(repo_module, "MAX_TOTAL_BYTES", 10)
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"a.md": b"x" * 1000})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    with pytest.raises(ValueError, match="exceeds"):
        await snap.refresh()
    assert not (tmp_path / "_repo" / SHA).exists()


@respx.mock
async def test_extraction_refuses_a_gzip_bomb(tmp_path, http):
    """A small compressed download (under the download cap) that expands to
    far more than MAX_TOTAL_BYTES once decompressed -- gzip's own repetition
    makes this cheap to produce and the compressed-size check alone would
    never catch it. Highly compressible content (all zero bytes) declared at
    well over the real MAX_TOTAL_BYTES, but small enough on the wire that
    the download-time cap does not trip first."""
    _mock_commit()
    _mock_commit_log([SHA])
    huge = b"\0" * (60 * 1024 * 1024)  # 60MB uncompressed, over the 50MB cap
    tar_bytes = _tar_bytes({"a.md": huge})
    assert len(tar_bytes) < 1024 * 1024, "the compressed tarball must stay small for this test"
    respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=tar_bytes)
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    with pytest.raises(ValueError, match="gzip bomb"):
        await snap.refresh()
    assert not (tmp_path / "_repo" / SHA).exists()


# -- sensitive files are never extracted ---------------------------------


@respx.mock
async def test_dotenv_is_never_extracted(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({".env": b"SECRET=do-not-write-me\n", "README.md": b"fine\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (state.root / ".env").exists()
    assert (state.root / "README.md").read_text() == "fine\n"


@respx.mock
async def test_a_private_key_is_never_extracted(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball(
        {"deploy/id_rsa": b"-----BEGIN OPENSSH PRIVATE KEY-----\n", "README.md": b"fine\n"}
    )

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (state.root / "deploy" / "id_rsa").exists()


@respx.mock
async def test_env_template_is_still_extracted(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({".env.example": b"SECRET=\n", "README.md": b"fine\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert (state.root / ".env.example").read_text() == "SECRET=\n"


@respx.mock
async def test_a_service_account_shaped_json_is_dropped_by_content(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    key_body = (
        b'{"type": "service_account", "project_id": "x", '
        b'"private_key": "-----BEGIN PRIVATE KEY-----"}'
    )
    _mock_tarball({"gcp-creds.json": key_body, "package.json": b'{"name": "x"}', "README.md": b"fine\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (state.root / "gcp-creds.json").exists()
    # An ordinary JSON file with no service-account markers is unaffected.
    assert (state.root / "package.json").exists()


@respx.mock
async def test_a_path_under_volumes_is_never_extracted(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"volumes/ai-brain-memory/brain/identity.md": b"private\n", "README.md": b"fine\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert not (state.root / "volumes").exists()


@respx.mock
async def test_a_script_that_mentions_secrets_in_its_name_is_still_extracted(tmp_path, http):
    """grafana/set-github-secrets.sh: a script is probably fine to show --
    it is source code whose job is handling secrets, not a secret itself.
    It still goes through repo.py's own redact() at read time (see
    tools/introspect.py), same as any other source file."""
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"grafana/set-github-secrets.sh": b"#!/bin/sh\necho hi\n"})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    state = await snap.refresh()

    assert (state.root / "grafana" / "set-github-secrets.sh").exists()


# -- adopting an existing snapshot on restart, without any network -------


@respx.mock
async def test_adopt_existing_finds_a_snapshot_a_previous_process_extracted(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"README.md": b"hi\n"})

    first = RepoSnapshot(tmp_path, SLUG, REF, http)
    await first.refresh()

    # A brand new instance, as a restarted process would construct -- no
    # refresh() call, so no network at all (respx would fail an unexpected
    # request if one happened).
    second = RepoSnapshot(tmp_path, SLUG, REF, http)
    adopted = second.adopt_existing()

    assert adopted is not None
    assert adopted.sha == SHA
    assert (second.state.root / "README.md").read_text() == "hi\n"
    assert second.state.fetched_at > 0


def test_adopt_existing_on_a_fresh_volume_returns_none():
    snap = RepoSnapshot(Path("/tmp/does-not-exist-ai-brain-test"), SLUG, REF, http=None)
    assert snap.adopt_existing() is None
    assert snap.state is None


def test_adopt_existing_ignores_a_pointer_that_is_not_a_symlink(tmp_path):
    repo_dir = tmp_path / "_repo"
    repo_dir.mkdir(parents=True)
    # A plain file named "current" -- not something this class ever wrote,
    # so adopting it is refused rather than guessed at.
    (repo_dir / "current").write_text("not a symlink", encoding="utf-8")

    snap = RepoSnapshot(tmp_path, SLUG, REF, http=None)
    assert snap.adopt_existing() is None
    assert snap.state is None


@respx.mock
async def test_refresh_does_not_re_download_an_adopted_sha(tmp_path, http):
    _mock_commit()
    _mock_commit_log([SHA])
    tar_route = respx.get(f"https://codeload.github.com/{SLUG}/tar.gz/{SHA}").mock(
        return_value=httpx.Response(200, content=_tar_bytes({"a.md": b"hi\n"}))
    )

    first = RepoSnapshot(tmp_path, SLUG, REF, http)
    await first.refresh()
    assert tar_route.call_count == 1

    second = RepoSnapshot(tmp_path, SLUG, REF, http)
    second.adopt_existing()
    await second.refresh()  # SHA unchanged from adoption -- must not re-download

    assert tar_route.call_count == 1


@respx.mock
async def test_refresh_never_deletes_the_directory_current_points_to(tmp_path, http):
    """A concurrent reader (a code_* tool call mid-cycle) can hold an open
    file handle into the adopted snapshot; refresh() must not rmtree it out
    from under that reader even when re-extracting the very same SHA."""
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"a.md": b"hi\n"})

    first = RepoSnapshot(tmp_path, SLUG, REF, http)
    await first.refresh()

    second = RepoSnapshot(tmp_path, SLUG, REF, http)
    second.adopt_existing()
    # Force a re-extraction of the same SHA (as if the adopted state were
    # considered stale) to exercise the "target == current_target" guard
    # rather than the unrelated "unchanged SHA" early return.
    second._state = None
    await second.refresh()

    assert (second.state.root / "a.md").exists()
