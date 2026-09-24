"""A read-only, on-disk snapshot of the iot-fetcher repo's tracked files.

The brain wants to read its own code and its siblings' -- why a service
behaves the way it does is often answered by the source, not a metric. The
rpi5 checkout is the obvious place to look, and the one place this must never
touch: it holds untracked secrets (``.env`` files, a service-account JSON, a
password file) that no agent may ever see, and there is no reliable way to
tell "tracked" from "untracked" by staring at a mounted directory.

The public GitHub tarball has no such problem -- ``codeload.github.com``
serves exactly what git has committed, nothing else, because that is the only
thing a tarball export can contain. So the snapshot is downloaded over HTTP
like any other backend read, extracted under ``/memory/_repo/<sha>/``, and a
``current`` symlink is swapped atomically once extraction succeeds -- a reader
mid-refresh always sees either the old snapshot or the new one, never a
half-written directory.

Tar safety is not optional here: the archive is *public* input (anyone can
push to a public GitHub mirror's default branch, and codeload serves whatever
HEAD is), so every member is checked before it touches disk -- regular files
only, no absolute or ``..`` names, no symlinks/hardlinks, and a total size
cap. ``tarfile``'s own ``data_filter`` (Python 3.12+) rejects most of this
already; the checks here are explicit anyway so the policy is readable in one
place rather than trusted to a library default that could change.

Defence in depth, not the only gate: a member matching ``ai_brain.sensitive``
(an ``.env`` file, a private key, a service-account-shaped JSON, ...) is
skipped here too, so it is never written to ``/memory/_repo`` at all. The
snapshot already only contains what git tracked -- a real secret on rpi5 is
never in codeload's tarball to begin with -- but a secret-shaped file can
still be committed by mistake, in this repo or a fork, and the day that
happens must not be the day it lands on disk for a tool to read back.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from ai_brain import sensitive
from ai_brain.sensitive import is_sensitive

log = logging.getLogger(__name__)

# codeload always serves a gzipped tarball for a ref; the commits API gives
# the exact SHA that tarball corresponds to, and the last N of them for
# code_log. Both are unauthenticated, public endpoints -- no token needed for
# a public repo, and none is sent.
CODELOAD_URL = "https://codeload.github.com/{slug}/tar.gz/{ref}"
COMMITS_URL = "https://api.github.com/repos/{slug}/commits/{ref}"

DOWNLOAD_TIMEOUT_S = 60
COMMITS_TIMEOUT_S = 20
MAX_LOG_COMMITS = 50

# A tar bomb is the obvious abuse of an unauthenticated download: refuse
# anything whose *declared* sizes sum past this before extracting a single
# byte. The real repo is a few MB of source; 50 MB is generous headroom
# without being an invitation to fill the memory volume.
MAX_TOTAL_BYTES = 50 * 1024 * 1024

# How many SHA directories _prune_old keeps in total (current plus the most
# recently modified others), so the _repo dir does not grow forever across
# restarts and refreshes. The current one is never counted against this by
# being pruned -- see _prune_old -- but it does occupy one of the slots.
KEEP_SNAPSHOTS = 2


@dataclass(frozen=True)
class Commit:
    sha: str
    date: str
    subject: str


@dataclass(frozen=True)
class RepoState:
    """What ``code_*`` tools need to know about the live snapshot."""

    sha: str
    fetched_at: float
    root: Path


class RepoSnapshot:
    """Owns the on-disk tarball snapshot and its ``current`` pointer.

    One instance lives for the process lifetime, shared by every ``code_*``
    tool call and by the periodic refresh job. ``refresh`` is safe to call
    concurrently with a reader: the pointer only ever moves forward, from one
    fully-extracted directory to another.
    """

    def __init__(
        self,
        memory_root: Path,
        slug: str,
        ref: str,
        http: httpx.AsyncClient,
    ) -> None:
        self.root = Path(memory_root) / "_repo"
        self.slug = slug
        self.ref = ref
        self.http = http
        self._state: RepoState | None = None
        self._commits: list[Commit] = []

    # -- reading ---------------------------------------------------------

    @property
    def state(self) -> RepoState | None:
        """The current snapshot, or ``None`` before the first refresh ever
        succeeded (or after every attempt so far has failed) -- unless
        ``adopt_existing`` found one already on disk from a previous run."""
        return self._state

    def commits(self, n: int) -> list[Commit]:
        """The most recent commits, newest first, cached by the last refresh."""
        return self._commits[: max(n, 0)]

    # -- adopting what a previous process already fetched -----------------

    def adopt_existing(self) -> RepoState | None:
        """Point ``state`` at an already-extracted ``current`` snapshot, if
        one is on disk -- without any network call.

        A restart otherwise loses code access until the next scheduled
        refresh succeeds, which can be hours away and, if GitHub happens to
        be unreachable right then, longer still. The SHA is read from the
        ``current`` symlink's target name (that is exactly what
        ``_swap_current`` names it), and ``fetched_at`` from the target
        directory's mtime -- both were set by the refresh that created it, so
        this is exact, not a guess. Called once at startup, before the first
        network refresh; ``refresh()`` itself also treats an adopted SHA as
        "unchanged" the same way it does one from its own last refresh, so a
        healthy GitHub does not re-download something already on disk.
        """
        pointer = self.root / "current"
        try:
            target = pointer.resolve(strict=True)
        except OSError:
            return None
        if not target.is_dir() or target.parent != self.root.resolve():
            # Not a symlink this class created, or it points somewhere odd
            # (a stale/hand-edited pointer) -- safer to start from nothing
            # than to adopt a directory this code did not itself extract.
            return None
        sha = target.name
        try:
            fetched_at = target.stat().st_mtime
        except OSError:
            return None
        state = RepoState(sha=sha, fetched_at=fetched_at, root=target)
        self._state = state
        return state

    # -- refreshing --------------------------------------------------------

    async def refresh(self, dest: Path | None = None) -> RepoState:
        """Fetch the current SHA, extract it if new, and swap ``current``.

        ``dest`` overrides where snapshots are written -- only ever used by
        the live smoke test script, which points this at a throwaway temp
        directory instead of ``/memory/_repo`` so it never touches the real
        volume. Production always extracts under ``self.root``.

        Raises on any failure (network, a malformed tarball, a resolved SHA
        the previous refresh already has). The caller -- the supervisor's
        periodic job -- is expected to catch this, log it and keep the
        previous snapshot; a tool call against a snapshot that was never
        refreshed reports "unavailable" rather than raising into a cycle.
        """
        root = Path(dest) if dest is not None else self.root
        sha = await self._resolve_sha()
        if self._state is not None and self._state.sha == sha and dest is None:
            # Unchanged since the last refresh (or since adopt_existing found
            # it on disk): nothing to download or extract, and re-fetching
            # the same tarball on every REPO_REFRESH_H tick would cost
            # bandwidth for no new state. The commit log is the same story --
            # skipped too when it is already populated for this SHA, so an
            # adopted snapshot that never had a network call yet still gets
            # its log filled in exactly once, not on every unchanged tick.
            if not self._commits:
                await self._refresh_commits(sha)
            return self._state

        target = root / sha
        # Never delete the directory `current` still points to while
        # extracting into it: a concurrent reader (a tool call mid-cycle)
        # holds file handles into exactly that tree, and this SHA already
        # being on disk only happens when it is the adopted/current one
        # (the "unchanged" branch above returns before reaching here for the
        # SHA this instance already knows about).
        current_target = self._current_target(root)
        if target.exists() and target != current_target:
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)

        try:
            await self._download_and_extract(sha, target)
        except Exception:
            if target != current_target:
                shutil.rmtree(target, ignore_errors=True)
            raise

        self._swap_current(root, sha)
        self._prune_old(root, sha)
        state = RepoState(sha=sha, fetched_at=time.time(), root=target)
        self._state = state
        # At the resolved SHA, not self.ref: REPO_REF (usually "main") keeps
        # moving, so a log fetched "at main" right after a refresh already
        # disagrees with the snapshot the moment another commit lands --
        # code_log would then show commits the snapshot does not actually
        # have. Pinning to `sha` is what keeps the two in agreement.
        await self._refresh_commits(sha)
        return state

    def _current_target(self, root: Path) -> Path | None:
        pointer = root / "current"
        try:
            return pointer.resolve(strict=True)
        except OSError:
            return None

    async def _resolve_sha(self) -> str:
        url = COMMITS_URL.format(slug=self.slug, ref=self.ref)
        response = await self.http.get(
            url,
            headers={"Accept": "application/vnd.github+json"},
            timeout=COMMITS_TIMEOUT_S,
        )
        response.raise_for_status()
        body = response.json()
        sha = body.get("sha") if isinstance(body, dict) else None
        if not isinstance(sha, str) or not sha:
            raise ValueError(f"commits API returned no sha for {self.slug}@{self.ref}")
        return sha

    async def _refresh_commits(self, sha: str) -> None:
        """Best-effort: ``code_log`` degrades to empty rather than failing a
        whole refresh over a second, non-essential API call.

        ``sha`` is the *resolved* snapshot commit, not ``self.ref`` -- a
        moving ref like ``main`` can gain a new commit between the refresh
        that resolved ``sha`` and this call, and history "starting from
        main" would then include commits the extracted snapshot does not
        actually have. Starting the log from the exact commit that was
        extracted is what keeps ``code_log`` in agreement with ``code_read``/
        ``code_grep``.
        """
        url = f"https://api.github.com/repos/{self.slug}/commits"
        try:
            response = await self.http.get(
                url,
                params={"sha": sha, "per_page": str(MAX_LOG_COMMITS)},
                headers={"Accept": "application/vnd.github+json"},
                timeout=COMMITS_TIMEOUT_S,
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.warning("[repo] could not refresh commit log: %s", exc)
            return
        if not isinstance(body, list):
            return
        commits = []
        for entry in body:
            if not isinstance(entry, dict):
                continue
            sha = entry.get("sha")
            commit = entry.get("commit") if isinstance(entry.get("commit"), dict) else {}
            message = str(commit.get("message") or "")
            author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
            date = str(author.get("date") or "")
            if isinstance(sha, str) and sha:
                commits.append(Commit(sha=sha, date=date, subject=message.splitlines()[0] if message else ""))
        self._commits = commits

    async def _download_and_extract(self, sha: str, target: Path) -> None:
        url = CODELOAD_URL.format(slug=self.slug, ref=sha)
        async with self.http.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_S) as response:
            response.raise_for_status()
            tmp_tar = target.with_suffix(".tar.gz.tmp")
            total = 0
            try:
                with tmp_tar.open("wb") as fh:
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_TOTAL_BYTES:
                            raise ValueError(
                                f"tarball for {self.slug}@{sha} exceeds {MAX_TOTAL_BYTES} bytes"
                            )
                        fh.write(chunk)
                # Off the event loop: decompressing and writing a few hundred
                # files takes seconds on the Pi, and every agent loop shares
                # this one event loop.
                await asyncio.to_thread(_safe_extract, tmp_tar, target)
            finally:
                tmp_tar.unlink(missing_ok=True)

    def _swap_current(self, root: Path, sha: str) -> None:
        pointer = root / "current"
        tmp_link = root / "current.tmp"
        tmp_link.unlink(missing_ok=True)
        # Relative target: the pointer and the sha dir it names both live
        # directly under `root`, so the symlink stays valid if the volume is
        # ever bind-mounted at a different host path.
        tmp_link.symlink_to(sha, target_is_directory=True)
        os.replace(tmp_link, pointer)

    def _prune_old(self, root: Path, keep_sha: str) -> None:
        """Delete SHA directories beyond ``KEEP_SNAPSHOTS``, newest first,
        never touching ``keep_sha`` (the one ``current`` now points to)."""
        if not root.exists():
            return
        candidates = [
            entry
            for entry in root.iterdir()
            if entry.is_dir() and entry.name != "current" and entry.name != keep_sha
        ]
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for entry in candidates[max(KEEP_SNAPSHOTS - 1, 0) :]:
            shutil.rmtree(entry, ignore_errors=True)


def _safe_extract(tar_path: Path, dest: Path) -> None:
    """Extract ``tar_path`` into ``dest``, refusing anything unsafe.

    codeload's tarball wraps everything in one top-level ``<repo>-<sha>/``
    directory; that prefix is stripped so ``dest`` holds the repo's own root
    (``README.md`` at ``dest/README.md``, not ``dest/iot-fetcher-<sha>/README.md``).

    Every member is checked before any bytes are written for it:

    * regular files and directories only -- no symlinks, hardlinks, devices
      or fifos, so nothing extracted can point outside ``dest`` or be replayed
      as a special file.
    * no absolute paths and no ``..`` segment, checked both before and after
      stripping the top-level prefix -- codeload's own names are trusted to be
      well-formed, but this is public input and the check is what makes that
      an assumption this code verifies rather than one it assumes.
    * the resolved path must stay under ``dest`` (``Path.resolve`` plus a
      prefix check), which is what actually catches a stripped name that
      still manages to traverse.
    * not ``ai_brain.sensitive.is_sensitive`` -- an ``.env``, a key file, a
      service-account-shaped JSON and the rest of that denylist never reach
      disk at all. A small ``.json`` member (under ``MAX_SNIFF_BYTES``) is
      read into memory to check its content too, since a service-account key
      does not always have a name that says so.
    * the *uncompressed* total, summed from every kept member's declared
      ``size``, must stay under ``MAX_TOTAL_BYTES`` -- checked before a
      single byte is extracted. ``_download_and_extract`` already caps the
      compressed download, but gzip can expand a small download into a huge
      one on disk (the classic zip/gzip-bomb shape), so the compressed size
      alone is not the real limit this module promises.
    """
    dest = dest.resolve()
    with tarfile.open(tar_path, mode="r:gz") as tar:
        members = tar.getmembers()
        prefix = _common_prefix(members)
        safe_members = []
        total_size = 0
        for member in members:
            if not (member.isfile() or member.isdir()):
                # Symlinks, hardlinks, devices, fifos: skip silently. A
                # tracked symlink is not something this snapshot needs to
                # serve, and refusing it outright is simpler than validating
                # where it points.
                continue
            name = member.name
            if name.startswith("/") or ".." in Path(name).parts:
                log.warning("[repo] refusing unsafe tar member: %s", name)
                continue
            relative = _strip_prefix(name, prefix)
            if relative is None:
                continue
            if not relative or relative == ".":
                continue
            if ".." in Path(relative).parts:
                log.warning("[repo] refusing unsafe tar member after stripping prefix: %s", name)
                continue
            target = (dest / relative).resolve()
            if target != dest and dest not in target.parents:
                log.warning("[repo] refusing tar member outside snapshot root: %s", name)
                continue
            if member.isfile():
                sniff = None
                if relative.endswith(".json") and member.size <= sensitive.MAX_SNIFF_BYTES:
                    extracted = tar.extractfile(member)
                    sniff = extracted.read() if extracted is not None else b""
                if is_sensitive(relative, sniff):
                    log.warning("[repo] excluding sensitive file from snapshot: %s", relative)
                    continue
                total_size += member.size
                if total_size > MAX_TOTAL_BYTES:
                    raise ValueError(
                        f"tarball extracts to more than {MAX_TOTAL_BYTES} bytes "
                        "(uncompressed) -- refusing as a likely gzip bomb"
                    )
            member.name = relative
            safe_members.append(member)

        # Python 3.12+: the stdlib's own belt-and-braces filter, on top of
        # the checks above rather than instead of them -- this module's
        # policy must be readable without knowing tarfile's filter semantics.
        extract_filter = getattr(tarfile, "data_filter", None)
        if extract_filter is not None:
            tar.extraction_filter = extract_filter
        tar.extractall(dest, members=safe_members)


def _common_prefix(members: list[tarfile.TarInfo]) -> str:
    """codeload's own top-level ``<repo>-<sha>/`` directory name, or "" if the
    archive is not shaped that way (defensive -- every real codeload tarball
    has one).

    Skips any member whose name is itself unsafe (absolute, or starting with
    a ``..`` segment) when picking the prefix -- such a member is refused
    outright by the per-member check below regardless, but if it happened to
    sort first, using its shape to *guess* the prefix would silently drop
    every legitimate member instead of just the malicious one.
    """
    for member in members:
        name = member.name
        if name.startswith("/") or (Path(name).parts and Path(name).parts[0] == ".."):
            continue
        parts = Path(name).parts
        if parts:
            return parts[0]
    return ""


def _strip_prefix(name: str, prefix: str) -> str | None:
    if not prefix:
        return name
    parts = Path(name).parts
    if not parts or parts[0] != prefix:
        return None
    return str(Path(*parts[1:])) if len(parts) > 1 else ""
