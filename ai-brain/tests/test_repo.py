import io
import tarfile

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
    respx.get(f"https://api.github.com/repos/{SLUG}/commits").mock(
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
async def test_refresh_prunes_old_snapshots(tmp_path, http):
    _mock_commit_log(["sha1"])
    _mock_tarball({"a.md": b"one\n"}, sha="sha1")
    respx.get(f"https://api.github.com/repos/{SLUG}/commits/{REF}").mock(
        return_value=httpx.Response(200, json={"sha": "sha1"})
    )
    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    await snap.refresh()

    respx.get(f"https://api.github.com/repos/{SLUG}/commits/{REF}").mock(
        return_value=httpx.Response(200, json={"sha": "sha2"})
    )
    _mock_tarball({"a.md": b"two\n"}, sha="sha2")
    _mock_commit_log(["sha2", "sha1"])
    await snap.refresh()

    repo_dir = tmp_path / "_repo"
    remaining = {p.name for p in repo_dir.iterdir() if p.is_dir() or p.is_symlink()}
    assert "sha1" not in remaining
    assert "sha2" in remaining


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
    from ai_brain import repo as repo_module

    monkeypatch.setattr(repo_module, "MAX_TOTAL_BYTES", 10)
    _mock_commit()
    _mock_commit_log([SHA])
    _mock_tarball({"a.md": b"x" * 1000})

    snap = RepoSnapshot(tmp_path, SLUG, REF, http)
    with pytest.raises(ValueError, match="exceeds"):
        await snap.refresh()
    assert not (tmp_path / "_repo" / SHA).exists()
