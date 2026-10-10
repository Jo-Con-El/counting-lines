"""Turn a history into plain data and write it as JSON or JavaScript."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from .history import Point


def build_data(history: list[Point], rev: str, include_lang: list[str] | None = None) -> dict:
    """Plain, self-describing data for consumers (charts, dashboards...).

    ``languages`` is ordered by code lines at the last point, descending.
    Each point's ``langs`` maps language -> [code, comment, blank].
    """
    keep = set(include_lang) if include_lang else None
    points = []
    for p in history:
        langs = {k: v for k, v in p.langs.items() if keep is None or k in keep}
        code, comment, blank = (sum(v[i] for v in langs.values()) for i in range(3))
        points.append({
            "sha": p.sha,
            "date": p.date,
            "code": code,
            "comment": comment,
            "blank": blank,
            "langs": langs,
        })
    last = points[-1]["langs"] if points else {}
    seen = {lang for p in points for lang in p["langs"]}
    order = sorted(seen, key=lambda lang: (-last.get(lang, [0])[0], lang))
    return {
        "generator": "counting-lines",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rev": rev,
        "languages": order,
        "points": points,
    }


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def write_json(data: dict, path: str) -> None:
    # One point per line: compact, yet diffs nicely when committed.
    head = {k: v for k, v in data.items() if k != "points"}
    lines = [json.dumps(head, ensure_ascii=False)[:-1] + ', "points": [']
    points = [json.dumps(p, ensure_ascii=False) for p in data["points"]]
    lines.append(",\n".join(points))
    lines.append("]}\n")
    _write(path, "\n".join(lines))


def write_js(data: dict, path: str, var: str = "LOC_DATA", esm: bool = False) -> None:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if esm:
        text = f"export const {var} = {body};\nexport default {var};\n"
    else:
        text = f"globalThis.{var} = {body};\n"
    _write(path, text)
