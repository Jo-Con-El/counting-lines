"""A single, self-updating progress line on a terminal."""

from __future__ import annotations

import sys
import time
from typing import TextIO


class Progress:
    """Call with (phase, done, total) as work advances; total may be None.

    Draws nothing unless the stream is a terminal. ``log`` prints a regular
    message without garbling the progress line.
    """

    def __init__(self, stream: TextIO = sys.stderr, interval: float = 0.1):
        self.stream = stream
        self.enabled = stream.isatty()
        self.interval = interval
        self.phase: str | None = None
        self.started = 0.0
        self.last_draw = 0.0
        self.drawn = False
        self.complete = False

    def __call__(self, phase: str, done: int, total: int | None = None) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if phase != self.phase:
            self.finish()
            self.phase, self.started = phase, now
        elif now - self.last_draw < self.interval and done != total:
            return
        self.last_draw = now

        self.complete = done == total
        if total:
            line = f"{phase}: {done:,}/{total:,} ({done * 100 // total}%)"
            elapsed = now - self.started
            if 0 < done < total and elapsed > 1:
                line += f", {_duration(elapsed / done * (total - done))} left"
        else:
            line = f"{phase}: {done:,}"
        self.stream.write(f"\r\x1b[K{line}")
        self.stream.flush()
        self.drawn = True

    def finish(self) -> None:
        """End the current progress line, leaving it on screen."""
        if self.drawn:
            self.stream.write("\n")
            self.stream.flush()
        self.drawn = False
        self.phase = None

    def log(self, msg: str) -> None:
        if self.drawn and self.complete:
            self.finish()
        elif self.drawn:
            # Clear the line; the next update redraws it below the message.
            self.stream.write("\r\x1b[K")
            self.drawn = False
            self.last_draw = 0.0
        print(msg, file=self.stream)


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h{minutes:02d}m"
