"""Incremental line-count history along the first-parent chain, with a cache."""

from __future__ import annotations

import fnmatch
import gzip
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterable

# (phase, done, total or None); see progress.Progress.
ProgressFn = Callable[[str, int, "int | None"], None]

from . import git, languages

CACHE_VERSION = 2

# Lock files, vendored dependencies and translation catalogs.
DEFAULT_EXCLUDE = ["package-lock.json", "node_modules", "*.po", "*.pot"]

# Path language of extensionless files: the blob's shebang decides.
SNIFF = "#!"


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
    dedupe: bool = False  # count identical files once per commit
    head: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # path -> blob sha
    blobs: dict[str, list[int] | None] = field(default_factory=dict)  # "sha:lang" -> stats, None if binary
    sniffed: dict[str, str | None] = field(default_factory=dict)  # blob sha -> shebang language
    history: list[Point] = field(default_factory=list)

    def reset(self) -> None:
        # Blob stats only depend on content and language, so they survive.
        self.head = None
        self.files = {}
        self.history = []

    @classmethod
    def load(cls, path: str, exclude: list[str], dedupe: bool = False) -> "Cache":
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, OSError, ValueError):
            return cls(exclude, dedupe)
        if data.get("version") != CACHE_VERSION or data.get("rules") != languages.RULES_VERSION:
            return cls(exclude, dedupe)
        cache = cls(
            exclude,
            dedupe,
            head=data["head"],
            files=data["files"],
            blobs=data["blobs"],
            sniffed=data["sniffed"],
            history=[Point.from_json(p) for p in data["history"]],
        )
        # The history depends on these settings; the blob stats do not.
        if data.get("exclude") != exclude or data.get("dedupe", False) != dedupe:
            cache.reset()
        return cache

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        data = {
            "version": CACHE_VERSION,
            "rules": languages.RULES_VERSION,
            "exclude": self.exclude,
            "dedupe": self.dedupe,
            "head": self.head,
            "files": self.files,
            "blobs": self.blobs,
            "sniffed": self.sniffed,
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


def path_language(path: str, exclude: list[str]) -> str | None:
    """Language implied by a path: a name, SNIFF, or None if not counted."""
    if is_excluded(path, exclude):
        return None
    if lang := languages.detect(path):
        return lang
    return SNIFF if languages.may_have_shebang(path) else None


def _resolve(
    blob: str, path_lang: str, blobs: dict, sniffed: dict
) -> tuple[str, list[int]] | None:
    """The language and stats of a blob found under a path language."""
    lang = sniffed.get(blob) if path_lang == SNIFF else path_lang
    stats = blobs.get(f"{blob}:{lang}") if lang else None
    return (lang, stats) if stats is not None else None


def _is_known(blob: str, path_lang: str, blobs: dict, sniffed: dict) -> bool:
    if path_lang == SNIFF:
        return blob in sniffed
    return f"{blob}:{path_lang}" in blobs


def _count_blobs(
    repo: str, wanted: dict[str, set[str]], blobs: dict, sniffed: dict,
    progress: ProgressFn | None = None,
) -> None:
    """Read and count ``wanted`` (blob -> path languages) in one cat-file stream."""
    for done, (sha, data) in enumerate(git.read_blobs(repo, wanted), 1):
        if progress:
            progress("counting blobs", done, len(wanted))
        binary = languages.is_binary(data)
        langs = set(wanted[sha])
        if SNIFF in langs:
            langs.discard(SNIFF)
            sniffed[sha] = lang = None if binary else languages.detect_shebang(data)
            if lang:
                langs.add(lang)
        for lang in langs:
            key = f"{sha}:{lang}"
            if key not in blobs:
                blobs[key] = None if binary else languages.count(data, lang).as_list()


def snapshot(repo: str, rev: str, exclude: list[str], dedupe: bool = False) -> dict[str, list[int]]:
    """Count a single tree from scratch, ignoring history and cache."""
    entries = []
    wanted: dict[str, set[str]] = {}
    for mode, blob, path in git.ls_tree(repo, rev):
        path_lang = path_language(path, exclude)
        if mode in git.FILE_MODES and path_lang:
            entries.append((blob, path_lang))
            wanted.setdefault(blob, set()).add(path_lang)
    blobs: dict = {}
    sniffed: dict = {}
    _count_blobs(repo, wanted, blobs, sniffed)
    totals: dict[str, list[int]] = {}
    seen: set[tuple[str, str]] = set()
    for blob, path_lang in entries:
        if resolved := _resolve(blob, path_lang, blobs, sniffed):
            lang, stats = resolved
            if dedupe:
                if (blob, lang) in seen:
                    continue
                seen.add((blob, lang))
            acc = totals.setdefault(lang, [0, 0, 0])
            for i in range(3):
                acc[i] += stats[i]
    return {k: v for k, v in sorted(totals.items()) if any(v)}


def _log(msg: str) -> None:
    print(msg, file=sys.stderr)


def _collect(commits: Iterable[git.Commit], progress: ProgressFn | None) -> list[git.Commit]:
    result = []
    for commit in commits:
        result.append(commit)
        if progress:
            progress("reading history", len(result), None)
    if progress:
        progress("reading history", len(result), len(result))
    return result


def update(
    repo: str,
    target: str,
    cache: Cache,
    log: Callable[[str], None] = _log,
    progress: ProgressFn | None = None,
) -> list[Point]:
    """Bring ``cache`` up to ``target`` and return the history ending there."""
    if cache.head == target:
        return cache.history
    for i, point in enumerate(cache.history):
        if point.sha == target:
            # An older commit already in the history: no work, cache untouched.
            return cache.history[: i + 1]

    def read_log(base: str | None) -> list[git.Commit]:
        return _collect(git.first_parent_log(repo, target, base), progress)

    commits = []
    if cache.head:
        try:
            commits = read_log(cache.head)
        except git.GitError:
            # E.g. garbage-collected after a rebase, or a cache from another repository.
            log(f"cached head {cache.head[:12]} does not exist in this repository; starting over")
            cache.reset()
    if cache.head and (not commits or commits[0].parents[:1] != [cache.head]):
        log(f"cached head {cache.head[:12]} is not on the first-parent chain of {target[:12]}; starting over")
        cache.reset()
    if cache.head is None:
        if git.is_shallow(repo):
            log("warning: shallow repository, history starts at the shallow boundary")
        commits = read_log(None)

    log(f"{len(commits)} new commit(s) to process")

    # Count every blob we have never seen, in a single cat-file stream.
    # The same blob can show up under several languages (e.g. renamed .h -> .c).
    wanted: dict[str, set[str]] = {}
    for commit in commits:
        for change in commit.changes:
            path_lang = path_language(change.path, cache.exclude) if change.new_blob else None
            if path_lang and not _is_known(change.new_blob, path_lang, cache.blobs, cache.sniffed):
                wanted.setdefault(change.new_blob, set()).add(path_lang)

    if wanted:
        log(f"reading {len(wanted)} new blob(s)")
    _count_blobs(repo, wanted, cache.blobs, cache.sniffed, progress)

    # Replay the diffs, keeping running totals per language. With dedupe, a
    # blob counts when its first copy appears and stops with its last one.
    totals: dict[str, list[int]] = {}
    copies: dict[tuple[str, str], int] = {}

    def apply(blob: str, path_lang: str, sign: int) -> None:
        resolved = _resolve(blob, path_lang, cache.blobs, cache.sniffed)
        if resolved is None:
            return
        lang, stats = resolved
        if cache.dedupe:
            key = (blob, lang)
            n = copies.get(key, 0) + sign
            if n:
                copies[key] = n
            else:
                del copies[key]
            if n != (1 if sign > 0 else 0):
                return  # not the first copy added, nor the last one removed
        acc = totals.setdefault(lang, [0, 0, 0])
        for i in range(3):
            acc[i] += sign * stats[i]

    for path, blob in cache.files.items():
        apply(blob, path_language(path, cache.exclude), +1)

    for done, commit in enumerate(commits, 1):
        if progress:
            progress("replaying commits", done, len(commits))
        for change in commit.changes:
            path_lang = path_language(change.path, cache.exclude)
            if path_lang is None:
                continue
            old = cache.files.pop(change.path, None)
            if old is not None:
                apply(old, path_lang, -1)
            if change.new_blob is not None:
                cache.files[change.path] = change.new_blob
                apply(change.new_blob, path_lang, +1)
        langs = {k: list(v) for k, v in sorted(totals.items()) if any(v)}
        cache.history.append(Point(commit.sha, commit.date, langs))
        cache.head = commit.sha

    return cache.history
