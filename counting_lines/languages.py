"""Language detection by file name and a cloc-like code/comment/blank counter."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

# Bump whenever detection or counting rules change: cached per-blob stats
# computed with an older version are discarded.
RULES_VERSION = 2


@dataclass(frozen=True)
class Language:
    name: str
    line: tuple[str, ...] = ()
    block: tuple[tuple[str, str], ...] = ()
    # String delimiters; comment markers between them are not comments.
    # Strings are assumed to end at the end of the line.
    quotes: tuple[str, ...] = ()


@dataclass
class Stats:
    code: int = 0
    comment: int = 0
    blank: int = 0

    def as_list(self) -> list[int]:
        return [self.code, self.comment, self.blank]


C_LIKE = dict(line=("//",), block=(("/*", "*/"),), quotes=('"', "'"))
C_LIKE_DQ = {**C_LIKE, "quotes": ('"',)}  # where ' is not a string (Rust lifetimes...)
JS_LIKE = {**C_LIKE, "quotes": ('"', "'", "`")}
HASH = dict(line=("#",))

_LANGS = [
    (Language("Python", line=("#",), block=(('"""', '"""'), ("'''", "'''")), quotes=('"', "'")), [".py", ".pyi", ".pyw"]),
    (Language("C", **C_LIKE), [".c"]),
    (Language("C/C++ Header", **C_LIKE), [".h", ".hh", ".hpp", ".hxx"]),
    (Language("C++", **C_LIKE), [".cc", ".cpp", ".cxx", ".c++"]),
    (Language("C#", **C_LIKE), [".cs"]),
    (Language("Objective-C", **C_LIKE), [".m", ".mm"]),
    (Language("Java", **C_LIKE), [".java"]),
    (Language("Kotlin", **C_LIKE), [".kt", ".kts"]),
    (Language("Scala", **C_LIKE_DQ), [".scala"]),
    (Language("Swift", **C_LIKE_DQ), [".swift"]),
    (Language("Go", **JS_LIKE), [".go"]),
    (Language("Rust", **C_LIKE_DQ), [".rs"]),
    (Language("Zig", line=("//",)), [".zig"]),
    (Language("Dart", **C_LIKE), [".dart"]),
    (Language("JavaScript", **JS_LIKE), [".js", ".mjs", ".cjs", ".jsx"]),
    (Language("TypeScript", **JS_LIKE), [".ts", ".mts", ".cts", ".tsx"]),
    (Language("PHP", line=("//", "#"), block=(("/*", "*/"),), quotes=('"', "'")), [".php"]),
    (Language("CSS", block=(("/*", "*/"),), quotes=('"', "'")), [".css"]),
    (Language("SCSS", **C_LIKE), [".scss"]),
    (Language("Less", **C_LIKE), [".less"]),
    (Language("HTML", block=(("<!--", "-->"),)), [".html", ".htm"]),
    (Language("XML", block=(("<!--", "-->"),)), [".xml", ".xsd", ".xsl", ".svg"]),
    (Language("Vue", line=("//",), block=(("<!--", "-->"), ("/*", "*/")), quotes=('"', "'", "`")), [".vue"]),
    (Language("Svelte", line=("//",), block=(("<!--", "-->"), ("/*", "*/")), quotes=('"', "'", "`")), [".svelte"]),
    (Language("Ruby", line=("#",), block=(("=begin", "=end"),)), [".rb", ".rake", ".gemspec"]),
    (Language("Perl", **HASH), [".pl", ".pm", ".t"]),
    (Language("Bourne Shell", **HASH), [".sh", ".bash", ".zsh", ".ksh"]),
    (Language("Fish", **HASH), [".fish"]),
    (Language("PowerShell", line=("#",), block=(("<#", "#>"),), quotes=('"', "'")), [".ps1", ".psm1"]),
    (Language("Lua", line=("--",), block=(("--[[", "]]"),), quotes=('"', "'")), [".lua"]),
    (Language("R", **HASH), [".r", ".R"]),
    (Language("Julia", line=("#",), block=(("#=", "=#"),), quotes=('"',)), [".jl"]),
    (Language("Haskell", line=("--",), block=(("{-", "-}"),), quotes=('"',)), [".hs", ".lhs"]),
    (Language("OCaml", block=(("(*", "*)"),), quotes=('"',)), [".ml", ".mli"]),
    (Language("Elixir", **HASH), [".ex", ".exs"]),
    (Language("Erlang", line=("%",)), [".erl", ".hrl"]),
    (Language("Clojure", line=(";",)), [".clj", ".cljs", ".cljc", ".edn"]),
    (Language("Lisp", line=(";",), block=(("#|", "|#"),), quotes=('"',)), [".lisp", ".el", ".scm"]),
    (Language("SQL", line=("--",), block=(("/*", "*/"),), quotes=('"', "'")), [".sql"]),
    (Language("Fortran", line=("!",)), [".f90", ".f95", ".f03", ".f08"]),
    (Language("Assembly", line=(";", "#", "//")), [".s", ".S", ".asm"]),
    (Language("Vim script", line=('"',)), [".vim"]),
    (Language("TeX", line=("%",)), [".tex", ".sty", ".cls"]),
    (Language("Markdown"), [".md", ".markdown"]),
    (Language("reStructuredText"), [".rst"]),
    (Language("YAML", **HASH), [".yml", ".yaml"]),
    (Language("TOML", **HASH), [".toml"]),
    (Language("INI", line=(";", "#")), [".ini", ".cfg"]),
    (Language("JSON"), [".json"]),
    (Language("Protocol Buffers", **C_LIKE), [".proto"]),
    (Language("GraphQL", **HASH), [".graphql", ".gql"]),
    (Language("Terraform", line=("#", "//"), block=(("/*", "*/"),), quotes=('"',)), [".tf", ".tfvars"]),
    (Language("Nix", line=("#",), block=(("/*", "*/"),), quotes=('"',)), [".nix"]),
    (Language("CMake", **HASH), [".cmake"]),
    (Language("make", **HASH), [".mk", ".mak"]),
    (Language("Dockerfile", **HASH), [".dockerfile"]),
]

_FILENAMES = {
    "Makefile": "make",
    "GNUmakefile": "make",
    "makefile": "make",
    "CMakeLists.txt": "CMake",
    "Dockerfile": "Dockerfile",
    "Containerfile": "Dockerfile",
    "Rakefile": "Ruby",
    "Gemfile": "Ruby",
    "Jenkinsfile": "Groovy",
}

LANGUAGES: dict[str, Language] = {lang.name: lang for lang, _ in _LANGS}
LANGUAGES["Groovy"] = Language("Groovy", **C_LIKE)
_EXTENSIONS: dict[str, str] = {ext: lang.name for lang, exts in _LANGS for ext in exts}


def detect(path: str) -> str | None:
    """Return the language name for a repository path, or None if unknown."""
    base = path.rsplit("/", 1)[-1]
    if base in _FILENAMES:
        return _FILENAMES[base]
    ext = os.path.splitext(base)[1]
    return _EXTENSIONS.get(ext) or _EXTENSIONS.get(ext.lower())


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8000]


def count(data: bytes, language: str) -> Stats:
    """Classify each line as code, comment or blank.

    Like cloc, this is a heuristic: comment markers inside single-line string
    literals are skipped, but strings spanning several lines are not tracked,
    and Python docstrings count as comments.
    """
    lang = LANGUAGES[language]
    stats = Stats()
    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()

    block_end: str | None = None
    for raw in lines:
        line = raw.strip()
        has_code = False
        has_comment = False

        if block_end is not None:
            idx = line.find(block_end)
            if idx < 0:
                stats.comment += 1 if line else 0
                stats.blank += 0 if line else 1
                continue
            line = line[idx + len(block_end):].strip()
            block_end = None
            has_comment = True

        while line:
            if any(line.startswith(m) for m in lang.line):
                has_comment = True
                break
            opener = next(((s, e) for s, e in lang.block if line.startswith(s)), None)
            if opener is not None:
                has_comment = True
                start, end = opener
                idx = line.find(end, len(start))
                if idx < 0:
                    block_end = end
                    break
                line = line[idx + len(end):].strip()
                continue
            # Real code on this line; only care whether a block comment
            # opens here and stays open past the end of the line.
            has_code = True
            block_end = _unclosed_block(line, lang)
            break

        if has_code:
            stats.code += 1
        elif has_comment:
            stats.comment += 1
        else:
            stats.blank += 1
    return stats


_SCANNERS: dict[str, re.Pattern[str]] = {}


def _scanner(lang: Language) -> re.Pattern[str]:
    """One regex finding the next block opener, line comment or string.

    At the same position, alternatives are tried in order, so block openers
    win over quotes (Python's triple quotes) and over line markers.
    """
    pattern = _SCANNERS.get(lang.name)
    if pattern is None:
        alts = [f"(?P<b{i}>{re.escape(start)})" for i, (start, _) in enumerate(lang.block)]
        if lang.line:
            alts.append(f"(?P<line>{'|'.join(map(re.escape, lang.line))})")
        for q in lang.quotes:
            e = re.escape(q)
            alts.append(f"(?:{e}(?:\\\\.|[^{e}\\\\])*(?:{e}|$))")
        pattern = _SCANNERS[lang.name] = re.compile("|".join(alts))
    return pattern


def _unclosed_block(line: str, lang: Language) -> str | None:
    """Return the closer of a block comment left open at the end of a code line."""
    if not lang.block:
        return None
    scanner = _scanner(lang)
    pos = 0
    while match := scanner.search(line, pos):
        kind = match.lastgroup
        if kind is None:  # a string literal
            pos = match.end()
        elif kind == "line":
            return None
        else:
            end = lang.block[int(kind[1:])][1]
            close = line.find(end, match.end())
            if close < 0:
                return end
            pos = close + len(end)
    return None
