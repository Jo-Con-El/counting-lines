"""Incremental line-count history along the first-parent chain, with a cache."""

from __future__ import annotations

import fnmatch
import gzip
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable

from . import git, languages

CACHE_VERSION = 1

# Lock files, vendored dependencies and translation catalogs.
DEFAULT_EXCLUDE = ["package-lock.json", "node_modules", "*.po", "*.pot"]


@dataclass
class Point:
    sha: str
    date: str
    langs: dict[str, list[int]]  # language -> [code, comment, blank]

    def to_json(self) -> dict:
        return {"sha": self.sha, "date": self.date, "langs": self.langs}

    @classmethod
    def from_json(cls, d: dict) -> "Point":
        return cls(d["sha"], d["date"], d["langs"])


@dataclass
class Cache:
    """Everything needed to resume from ``head`` without rereading history."""

    exclude: list[str]
    head: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # path -> blob sha
    blobs: dict[str, list[int] | None] = field(default_factory=dict)  # "sha:lang" -> stats, None if binary
    history: list[Point] = field(default_factory=list)

    def reset(self) -> None:
        # Blob stats only depend on content and language, so they survive.
        self.head = None
        self.files = {}
        self.history = []

    @classmethod
    def load(cls, path: str, exclude: list[str]) -> "Cache":
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, OSError, ValueError):
            return cls(exclude)
        if data.get("version") != CACHE_VERSION or data.get("rules") != languages.RULES_VERSION:
            return cls(exclude)
        cache = cls(
            exclude,
            head=data["head"],
            files=data["files"],
            blobs=data["blobs"],
            history=[Point.from_json(p) for p in data["history"]],
        )
        if data.get("exclude") != exclude:
            cache.reset()
        return cache

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = {
            "version": CACHE_VERSION,
            "rules": languages.RULES_VERSION,
            "exclude": self.exclude,
            "head": self.head,
            "files": self.files,
            "blobs": self.blobs,
            "history": [p.to_json() for p in self.history],
        }
        tmp = f"{path}.tmp"
        with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6) as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)


def is_excluded(path: str, patterns: list[str]) -> bool:
    """A pattern without '/' matches any path component (directory or file
    name); one with '/' matches the full path or one of its leading directories."""
    parts = path.split("/")
    for pat in patterns:
        if "/" in pat.strip("/"):
            pat = pat.strip("/")
            if any(fnmatch.fnmatchcase("/".join(parts[:i]), pat) for i in range(1, len(parts) + 1)):
                return True
        elif any(fnmatch.fnmatchcase(part, pat.strip("/")) for part in parts):
            return True
    return False


def snapshot(repo: str, rev: str, exclude: list[str]) -> dict[str, list[int]]:
    """Count a single tree from scratch, ignoring history and cache."""
    entries = []
    for mode, blob, path in git.ls_tree(repo, rev):
        lang = None if is_excluded(path, exclude) else languages.detect(path)
        if mode in git.FILE_MODES and lang:
            entries.append((blob, lang))
    blobs = dict(git.read_blobs(repo, {blob for blob, _ in entries}))
    totals: dict[str, list[int]] = {}
    for blob, lang in entries:
        if languages.is_binary(blobs[blob]):
            continue
        stats = languages.count(blobs[blob], lang).as_list()
        acc = totals.setdefault(lang, [0, 0, 0])
        for i in range(3):
            acc[i] += stats[i]
    return {k: v for k, v in sorted(totals.items()) if any(v)}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def update(
    repo: str,
    target: str,
    cache: Cache,
    log: Callable[[str], None] = _log,
) -> list[Point]:
    """Bring ``cache`` up to ``target`` and return the history ending there."""
    if cache.head == target:
        return cache.history
    for i, point in enumerate(cache.history):
        if point.sha == target:
            # An older commit already in the history: no work, cache untouched.
            return cache.history[: i + 1]

    commits = list(git.first_parent_log(repo, target, cache.head)) if cache.head else []
    if cache.head and (not commits or commits[0].parents[:1] != [cache.head]):
        log(f"cached head {cache.head[:12]} is not on the first-parent chain of {target[:12]}; starting over")
        cache.reset()
    if cache.head is None:
        if git.is_shallow(repo):
            log("warning: shallow repository, history starts at the shallow boundary")
        commits = list(git.first_parent_log(repo, target))

    log(f"{len(commits)} new commit(s) to process")

    def language_of(path: str) -> str | None:
        if is_excluded(path, cache.exclude):
            return None
        return languages.detect(path)

    # Count every blob we have never seen, in a single cat-file stream.
    # The same blob can show up under several languages (e.g. renamed .h -> .c).
    wanted: dict[str, set[str]] = {}
    for commit in commits:
        for change in commit.changes:
            lang = language_of(change.path) if change.new_blob else None
            if lang and f"{change.new_blob}:{lang}" not in cache.blobs:
                wanted.setdefault(change.new_blob, set()).add(lang)

    if wanted:
        log(f"reading {len(wanted)} new blob(s)")
    for sha, data in git.read_blobs(repo, wanted):
        binary = languages.is_binary(data)
        for lang in wanted[sha]:
            cache.blobs[f"{sha}:{lang}"] = None if binary else languages.count(data, lang).as_list()

    # Replay the diffs, keeping running totals per language.
    totals: dict[str, list[int]] = {}

    def apply(path: str, blob: str, sign: int) -> None:
        lang = language_of(path)
        stats = cache.blobs.get(f"{blob}:{lang}") if lang else None
        if stats is None:
            return
        acc = totals.setdefault(lang, [0, 0, 0])
        for i in range(3):
            acc[i] += sign * stats[i]

    for path, blob in cache.files.items():
        apply(path, blob, +1)

    for commit in commits:
        for change in commit.changes:
            if language_of(change.path) is None:
                continue
            old = cache.files.pop(change.path, None)
            if old is not None:
                apply(change.path, old, -1)
            if change.new_blob is not None:
                cache.files[change.path] = change.new_blob
                apply(change.path, change.new_blob, +1)
        langs = {k: list(v) for k, v in sorted(totals.items()) if any(v)}
        cache.history.append(Point(commit.sha, commit.date, langs))
        cache.head = commit.sha

    return cache.history
