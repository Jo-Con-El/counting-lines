"""Static SVG chart: code lines over time, stacked by language."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
from typing import Callable

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


# Candidate spacings between date labels, smallest first.
_TIME_STEPS = [
    ("hour", 1), ("hour", 3), ("hour", 6), ("hour", 12),
    ("day", 1), ("day", 2), ("day", 7), ("day", 14),
    ("month", 1), ("month", 2), ("month", 3), ("month", 6),
    ("year", 1), ("year", 2), ("year", 5), ("year", 10), ("year", 25),
]
_UNIT_SECONDS = {"hour": 3600, "day": 86400, "month": 30.44 * 86400, "year": 365.25 * 86400}
_UNIT_FORMAT = {"hour": "%H:%M", "day": "%Y-%m-%d", "month": "%Y-%m", "year": "%Y"}



def label_room(label: str) -> float:
    """Horizontal pixels to reserve for an 11px axis label, gap included."""
    return 6.6 * len(label) + 22


def _format_for(unit: str, span: float) -> str:
    if unit == "hour" and span > 86400:
        return "%m-%d %H:%M"
    return _UNIT_FORMAT[unit]


def time_ticks(lo: float, hi: float, room: float) -> tuple[list[datetime], str]:
    """Round calendar instants (UTC) between two timestamps whose labels fit in
    ``room`` pixels, as many as possible.

    Returns the instants and the strftime format that suits their spacing.
    """
    span = max(hi - lo, 1)

    def fits(unit: str, step: int) -> bool:
        sample = datetime(2000, 12, 28, 23, 59).strftime(_format_for(unit, span))
        return span / (_UNIT_SECONDS[unit] * step) * label_room(sample) <= room

    unit, step = next(((u, n) for u, n in _TIME_STEPS if fits(u, n)), _TIME_STEPS[-1])
    t = datetime.fromtimestamp(lo, timezone.utc)
    if unit == "hour":
        t = t.replace(hour=t.hour - t.hour % step, minute=0, second=0, microsecond=0)
    elif unit == "day":
        t = t.replace(hour=0, minute=0, second=0, microsecond=0)
    elif unit == "month":
        t = t.replace(month=t.month - (t.month - 1) % step, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        t = t.replace(year=t.year - t.year % step, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    ticks = []
    while t.timestamp() <= hi:
        if t.timestamp() >= lo:
            ticks.append(t)
        if unit == "hour":
            t += timedelta(hours=step)
        elif unit == "day":
            t += timedelta(days=step)
        elif unit == "month":
            months = t.month - 1 + step
            t = t.replace(year=t.year + months // 12, month=months % 12 + 1)
        else:
            t = t.replace(year=t.year + step)

    return ticks, _format_for(unit, span)


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

        def sx(stamp: float) -> float:
            return x0 + (stamp - lo) / ((hi - lo) or 1) * plot_w

        xs = [sx(s) for s in stamps]
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

    # X labels: round calendar dates on a time axis, evenly spaced commits otherwise.
    labels: list[tuple[float, str]] = []
    if x_axis == "time" and hi > lo:
        ticks_x, fmt = time_ticks(lo, hi, plot_w)
        fmt = date_format or fmt
        labels = [(sx(t.timestamp()), t.strftime(fmt)) for t in ticks_x]
    else:
        span_days = (times[-1] - times[0]).total_seconds() / 86400
        fmt = date_format or ("%H:%M" if span_days <= 2 else "%Y-%m-%d")
        room = label_room(times[0].strftime(fmt))
        n_labels = min(max(2, int(plot_w // room)), len(keep))
        for k in range(n_labels):
            j = round(k * (len(keep) - 1) / max(1, n_labels - 1))
            labels.append((xs[j], times[keep[j]].strftime(fmt)))
    for x, label in labels:
        parts.append(f'<line class="grid" x1="{x:.1f}" x2="{x:.1f}" y1="{pad_t}" y2="{y0}" stroke-width="1" stroke-opacity="0.5"/>')
        # Keep labels inside the image: anchor them inwards near the edges.
        half = 3.3 * len(label)
        anchor = "start" if x - half < 4 else "end" if x + half > width - 4 else "middle"
        parts.append(f'<text class="muted" x="{x:.1f}" y="{y0 + 18}" font-size="11" text-anchor="{anchor}">{escape(label)}</text>')

    def steps(level: Callable[[list[int]], float]) -> list[str]:
        """Vertices of a step line: a count holds until the next commit."""
        out = []
        for k, (x, row) in enumerate(zip(xs, stacks)):
            if k:
                out.append(f"{x:.1f},{sy(level(stacks[k - 1])):.1f}")
            out.append(f"{x:.1f},{sy(level(row)):.1f}")
        return out

    # Stacked areas, drawn top series first so lower ones overlay cleanly.
    for idx in reversed(range(len(series))):
        upper = steps(lambda row: row[idx])
        lower = steps(lambda row: row[idx - 1] if idx else 0)
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
