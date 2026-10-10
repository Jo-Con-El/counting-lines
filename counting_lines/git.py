"""Minimal git plumbing: a constant number of processes, never one per commit."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from typing import Iterable, Iterator

NULL_SHA = "0" * 40
# Only regular files are counted: no symlinks (120000) or submodules (160000).
FILE_MODES = {"100644", "100755"}


class GitError(RuntimeError):
    pass


@dataclass
class Change:
    path: str
    old_blob: str | None  # None when the old side is not a counted file
    new_blob: str | None  # None when the new side is not a counted file


@dataclass
class Commit:
    sha: str
    parents: list[str]
    date: str  # committer date, strict ISO 8601
    changes: list[Change] = field(default_factory=list)


def git(repo: str, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, *args], capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def rev_parse(repo: str, rev: str) -> str:
    return git(repo, "rev-parse", "--verify", "--end-of-options", f"{rev}^{{commit}}")


def is_shallow(repo: str) -> bool:
    return git(repo, "rev-parse", "--is-shallow-repository") == "true"


def first_parent_log(repo: str, target: str, base: str | None = None) -> Iterator[Commit]:
    """Yield the first-parent chain up to ``target``, oldest first.

    Each commit carries its raw diff against its first parent (or against the
    empty tree for a root commit), so totals can be updated incrementally.
    ``base`` excludes it and its ancestors.
    """
    cmd = [
        "git", "-C", repo,
        "-c", "log.showSignature=false",
        "log", "--first-parent", "--reverse", "--diff-merges=first-parent",
        "--root", "--raw", "--no-renames", "--no-abbrev", "-z",
        "--format=%x01%H %P%x00%cI",
        target,
    ]
    if base:
        cmd.append(f"^{base}")
    cmd.append("--")

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdout is not None
    try:
        yield from _parse_log(_nul_tokens(proc.stdout))
    finally:
        proc.stdout.close()
        assert proc.stderr is not None
        stderr = proc.stderr.read().decode(errors="replace")
        proc.stderr.close()
        if proc.wait() != 0:
            raise GitError(f"git log: {stderr.strip()}")


def _nul_tokens(stream, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    rest = b""
    while chunk := stream.read(chunk_size):
        parts = (rest + chunk).split(b"\0")
        rest = parts.pop()
        yield from parts
    if rest:
        yield rest


def _parse_log(tokens: Iterator[bytes]) -> Iterator[Commit]:
    current: Commit | None = None
    for token in tokens:
        token = token.lstrip(b"\n")
        if not token:
            continue
        if token.startswith(b"\x01"):
            if current is not None:
                yield current
            sha, *parents = token[1:].decode().split()
            current = Commit(sha, parents, next(tokens).decode())
        elif token.startswith(b":"):
            assert current is not None
            old_mode, new_mode, old_sha, new_sha, _status = token[1:].decode().split(" ")
            path = next(tokens).decode("utf-8", errors="surrogateescape")
            old = old_sha if old_mode in FILE_MODES and old_sha != NULL_SHA else None
            new = new_sha if new_mode in FILE_MODES and new_sha != NULL_SHA else None
            if old != new:
                current.changes.append(Change(path, old, new))
        else:
            raise GitError(f"unexpected git log token: {token[:80]!r}")
    if current is not None:
        yield current


def ls_tree(repo: str, rev: str) -> Iterator[tuple[str, str, str]]:
    """Yield (mode, sha, path) for every entry of the tree at ``rev``."""
    out = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "-z", "--full-tree", rev],
        capture_output=True,
    )
    if out.returncode != 0:
        raise GitError(f"git ls-tree: {out.stderr.decode(errors='replace').strip()}")
    for record in out.stdout.split(b"\0"):
        if record:
            meta, path = record.split(b"\t", 1)
            mode, _type, sha = meta.decode().split()
            yield mode, sha, path.decode("utf-8", errors="surrogateescape")


def read_blobs(repo: str, shas: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    """Stream blob contents through a single ``git cat-file --batch``."""
    proc = subprocess.Popen(
        ["git", "-C", repo, "cat-file", "--batch"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    assert proc.stdin is not None and proc.stdout is not None
    shas = list(shas)

    # Feed requests from another thread so neither pipe can fill up and deadlock.
    def feed() -> None:
        try:
            for sha in shas:
                proc.stdin.write(sha.encode() + b"\n")
            proc.stdin.close()
        except BrokenPipeError:
            pass

    writer = threading.Thread(target=feed, daemon=True)
    writer.start()
    try:
        for sha in shas:
            header = proc.stdout.readline().decode().split()
            if len(header) != 3 or header[1] != "blob":
                raise GitError(f"cat-file: unexpected reply for {sha}: {header}")
            size = int(header[2])
            data = proc.stdout.read(size)
            proc.stdout.read(1)  # trailing LF
            yield sha, data
    finally:
        writer.join()
        proc.stdout.close()
        proc.wait()
