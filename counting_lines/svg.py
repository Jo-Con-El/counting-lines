"""Static SVG chart: code lines over time, stacked by language."""

from __future__ import annotations

from datetime import datetime
from html import escape

# Fixed slots so a language keeps its colour as long as its rank does.
PALETTE = {
    "light": ["#2563eb", "#16a34a", "#d97706", "#db2777", "#7c3aed", "#0891b2", "#dc2626", "#65a30d"],
    "dark": ["#60a5fa", "#4ade80", "#fbbf24", "#f472b6", "#a78bfa", "#22d3ee", "#f87171", "#a3e635"],
}
OTHER = {"light": "#9ca3af", "dark": "#6b7280"}
BASE = {
    "light": {"bg": "#ffffff", "fg": "#1f2328", "muted": "#59636e", "grid": "#d1d9e0"},
    "dark": {"bg": "#0d1117", "fg": "#e6edf3", "muted": "#9198a1", "grid": "#30363d"},
}

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"


def _css(theme: str) -> str:
    rules = []

    def block(mode: str) -> str:
        b = BASE[mode]
        lines = [
            f".bg{{fill:{b['bg']}}}",
            f".fg{{fill:{b['fg']}}}",
            f".muted{{fill:{b['muted']}}}",
            f".grid{{stroke:{b['grid']}}}",
            f".s-other{{fill:{OTHER[mode]}}}",
        ]
        lines += [f".s{i}{{fill:{c}}}" for i, c in enumerate(PALETTE[mode])]
        return "".join(lines)

    if theme == "auto":
        rules.append(block("light"))
        rules.append(f"@media (prefers-color-scheme: dark){{{block('dark')}}}")
    else:
        rules.append(block(theme))
    rules.append(f"text{{font-family:{FONT}}}")
    return "".join(rules)


def nice_ticks(vmax: float, count: int = 4) -> list[int]:
    if vmax <= 0:
        return [0, 1]
    raw = vmax / count
    mag = 10 ** (len(str(int(raw))) - 1) if raw >= 1 else 1
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)
    step = max(1, int(step))
    top = -(-int(vmax) // step) * step
    return list(range(0, top + 1, step))


def fmt_num(n: int) -> str:
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            v = n / div
            return f"{v:.0f}{suffix}" if v == int(v) else f"{v:.1f}{suffix}"
    return str(n)


def render(
    data: dict,
    theme: str = "auto",
    top: int = 6,
    x_axis: str = "time",
    width: int = 900,
    height: int = 300,
    title: str = "Lines of code over time",
    date_format: str | None = None,
) -> str:
    points = data["points"]
    pad_l, pad_r, pad_t, pad_b = 52, 16, 40, 34
    legend_h = 22
    h_total = height + legend_h
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h_total}" '
        f'viewBox="0 0 {width} {h_total}" role="img" aria-label="{escape(title)}">'
        f"<style>{_css(theme)}</style>"
        f'<rect class="bg" width="100%" height="100%" rx="6"/>'
        f'<text class="fg" x="{pad_l}" y="24" font-size="14" font-weight="600">{escape(title)}</text>'
    )
    if not points:
        return head + f'<text class="muted" x="{pad_l}" y="{height / 2}" font-size="12">No data</text></svg>\n'

    # Series: the top languages at the last point, everything else lumped.
    shown = data["languages"][:top]
    rest = data["languages"][top:]
    series = [(lang, f"s{i}") for i, lang in enumerate(shown)]
    if rest:
        series.append(("Other", "s-other"))

    def values(p: dict) -> list[int]:
        langs = p["langs"]
        vals = [langs.get(lang, [0])[0] for lang in shown]
        if rest:
            vals.append(sum(langs.get(lang, [0])[0] for lang in rest))
        return vals

    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    x0, y0 = pad_l, pad_t + plot_h

    # X coordinates: committer time (kept monotonic) or commit index.
    times = [datetime.fromisoformat(p["date"]) for p in points]
    if x_axis == "time":
        stamps, last = [], float("-inf")
        for t in times:
            last = max(last, t.timestamp())
            stamps.append(last)
        lo, hi = stamps[0], stamps[-1]
        xs = [x0 + (s - lo) / ((hi - lo) or 1) * plot_w for s in stamps]
    else:
        n = len(points)
        xs = [x0 + i / max(1, n - 1) * plot_w for i in range(n)]

    # Drop points that would land on the same half-pixel column.
    keep, last_col = [], None
    for i, x in enumerate(xs):
        col = round(x * 2)
        if col == last_col and keep:
            keep[-1] = i
        else:
            keep.append(i)
        last_col = col
    xs = [xs[i] for i in keep]
    stacks = []
    for i in keep:
        acc, row = 0, []
        for v in values(points[i]):
            acc += v
            row.append(acc)
        stacks.append(row)

    ticks = nice_ticks(max(r[-1] for r in stacks) if stacks else 0)
    ymax = ticks[-1] or 1

    def sy(v: float) -> float:
        return y0 - v / ymax * plot_h

    parts = [head]
    for t in ticks:
        y = sy(t)
        parts.append(f'<line class="grid" x1="{x0}" x2="{x0 + plot_w}" y1="{y:.1f}" y2="{y:.1f}" stroke-width="1"/>')
        parts.append(f'<text class="muted" x="{x0 - 8}" y="{y + 4:.1f}" font-size="11" text-anchor="end">{fmt_num(t)}</text>')

    # X labels.
    span_days = (times[-1] - times[0]).total_seconds() / 86400
    fmt = date_format or ("%H:%M" if span_days <= 2 else "%Y-%m-%d")
    n_labels = min(6, len(keep))
    for k in range(n_labels):
        j = round(k * (len(keep) - 1) / max(1, n_labels - 1))
        x = xs[j]
        anchor = "start" if k == 0 else "end" if k == n_labels - 1 else "middle"
        label = times[keep[j]].strftime(fmt)
        parts.append(f'<text class="muted" x="{x:.1f}" y="{y0 + 18}" font-size="11" text-anchor="{anchor}">{escape(label)}</text>')

    # Stacked areas, drawn top series first so lower ones overlay cleanly.
    for idx in reversed(range(len(series))):
        upper = [f"{x:.1f},{sy(row[idx]):.1f}" for x, row in zip(xs, stacks)]
        lower = [f"{x:.1f},{sy(row[idx - 1] if idx else 0):.1f}" for x, row in zip(xs, stacks)]
        if len(xs) == 1:  # a single commit: draw a thin bar
            x = xs[0]
            upper = [f"{x - 2:.1f},{sy(stacks[0][idx]):.1f}", f"{x + 2:.1f},{sy(stacks[0][idx]):.1f}"]
            lower = [f"{x - 2:.1f},{sy(stacks[0][idx - 1] if idx else 0):.1f}", f"{x + 2:.1f},{sy(stacks[0][idx - 1] if idx else 0):.1f}"]
        d = "M" + " L".join(upper + lower[::-1]) + " Z"
        parts.append(f'<path class="{series[idx][1]}" d="{d}" fill-opacity="0.85"/>')

    # Total at the latest point.
    total = data["points"][-1]["code"]
    parts.append(
        f'<text class="fg" x="{x0 + plot_w}" y="24" font-size="13" text-anchor="end">'
        f"{total:,} lines</text>"
    )

    # Legend.
    lx, ly = x0, height + 8
    last_vals = values(points[-1])
    for (name, cls), v in zip(series, last_vals):
        label = f"{name} {fmt_num(v)}"
        parts.append(f'<rect class="{cls}" x="{lx}" y="{ly - 9}" width="10" height="10" rx="2"/>')
        parts.append(f'<text class="fg" x="{lx + 14}" y="{ly}" font-size="11">{escape(label)}</text>')
        lx += 22 + 6.5 * len(label)

    parts.append("</svg>\n")
    return "".join(parts)
