"""
A timer for measuring code performance, particularly in web servers.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from time import perf_counter
from typing import List

from pydantic import BaseModel


class Timing(BaseModel):
    name: str
    delta: float
    total: float
    time: float


class CodeTimer:
    timings: List[Timing] = []

    def __init__(self):
        self.timings = [Timing(name="start", delta=0, total=0, time=perf_counter())]

    def step(self, name: str):
        """Add a named step to the timer."""
        self._add_step(name)

    def _add_step(self, name: str) -> Timing:
        last_step = self.timings[-1]
        t = perf_counter()
        rec = Timing(
            name=name, delta=t - last_step.time, total=t - self.timings[0].time, time=t
        )
        self.timings.append(rec)
        return rec

    def server_timings(self):
        self._add_step("end")
        timings = [f"{t.name};dur={round(t.delta*1000, 1)}" for t in self.timings[1:-1]]
        timings.append(f"total;dur={round(self.timings[-1].total*1000, 1)}")
        return ", ".join(timings)


# Context variable to hold the current timer instance for the
# contextual timer. This is deprecated.
code_timer = ContextVar("code_timer", default=None)


class ContextualTimer(CodeTimer):
    @classmethod
    def add_step(cls, name: str):
        timer = code_timer.get()
        if timer is None:
            return
        timer._add_step(name)

    @contextmanager
    def context(self):
        token = code_timer.set(self)
        try:
            yield self
        finally:
            code_timer.reset(token)


# Legacy support for the contextual timer
# We use the contextual timer as the default, but prefer the basic timer
# for its simplicity for most use cases.
Timer = ContextualTimer
