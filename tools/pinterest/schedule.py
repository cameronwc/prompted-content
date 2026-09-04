"""Ramped publish schedule. Protects a new account from spam flagging.

Default ramp: week 1 5/day, week 2 8/day, week 3 12/day, week 4+ 25/week
spread across the week. Times fall between 06:00 and 20:00 local with
seeded jitter; no two pins ever share a minute.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

DEFAULT_RAMP = [
    {"days": 7, "per_day": 5},
    {"days": 7, "per_day": 8},
    {"days": 7, "per_day": 12},
    {"per_week": 25},          # open-ended tail
]
WINDOW_START, WINDOW_END = time(6, 0), time(20, 0)


def per_day_for(day_index: int, ramp: list[dict], override: int | None) -> int:
    if override is not None:
        return override
    offset = 0
    for stage in ramp:
        days = stage.get("days")
        if days is None:  # tail: spread per_week across 7 days
            per_week = int(stage["per_week"])
            pos = (day_index - offset) % 7
            base, extra = divmod(per_week, 7)
            return base + (1 if pos < extra else 0)
        if day_index < offset + days:
            return int(stage["per_day"])
        offset += days
    return 0


@dataclass
class Scheduler:
    start: date
    ramp: list[dict] = field(default_factory=lambda: list(DEFAULT_RAMP))
    pins_per_day: int | None = None
    used: set[datetime] = field(default_factory=set)
    counts: dict[date, int] = field(default_factory=dict)
    window: tuple[time, time] = (WINDOW_START, WINDOW_END)
    seed: str = "prompted-pins"

    def _slot(self, day: date, n: int, per_day: int) -> datetime:
        """The n-th of per_day evenly spaced slots on `day`, jittered."""
        start = datetime.combine(day, self.window[0])
        span = (datetime.combine(day, self.window[1]) - start).total_seconds()
        step = span / max(per_day, 1)
        rng = random.Random(f"{self.seed}:{day.isoformat()}:{n}")
        jitter = rng.uniform(0.1, 0.9) * step
        t = start + timedelta(seconds=int(n * step + jitter))
        t = t.replace(second=0, microsecond=0)
        while t in self.used:  # never the same minute twice
            t += timedelta(minutes=1)
        return t

    def peek(self) -> datetime:
        """The next free slot, without taking it."""
        day, idx = self.start, 0
        while True:
            cap = per_day_for(idx, self.ramp, self.pins_per_day)
            n = self.counts.get(day, 0)
            if n < cap:
                return self._slot(day, n, cap)
            day += timedelta(days=1)
            idx += 1
            if idx > 3650:
                raise RuntimeError("schedule ran past ten years; check the ramp config")

    def take(self, t: datetime) -> datetime:
        self.counts[t.date()] = self.counts.get(t.date(), 0) + 1
        self.used.add(t)
        return t

    def next(self) -> datetime:
        return self.take(self.peek())

    @classmethod
    def resume(cls, start: date, existing: list[datetime], **kw) -> "Scheduler":
        s = cls(start=start, **kw)
        for t in existing:
            s.used.add(t)
            s.counts[t.date()] = s.counts.get(t.date(), 0) + 1
        return s


# -- shoot diversity ---------------------------------------------------------

def slot_allowed(t: datetime, shoot_times: list[datetime], min_days_apart: int,
                 max_per_window: int, window_days: int) -> bool:
    """Would scheduling a pin from a shoot at `t` keep that shoot within the
    diversity rules given its other scheduled times?"""
    for other in shoot_times:
        if abs((other.date() - t.date()).days) < min_days_apart:
            return False
    # Rolling window: any window of `window_days` containing t must hold at
    # most max_per_window pins including this one.
    times = sorted(shoot_times + [t])
    span = timedelta(days=window_days)
    for i, a in enumerate(times):
        inside = sum(1 for b in times[i:] if b - a < span)
        if inside > max_per_window:
            return False
    return True


def assign_slots(items: list, scheduler: Scheduler, shoot_of, existing: dict[str, list[datetime]],
                 rules: dict, allow=None, horizon_days: int | None = None,
                 ) -> tuple[list[tuple[object, datetime]], list[tuple[object, str]]]:
    """Hand out slots in order while honouring the shoot-diversity rules.

    `items` are in selection order (cohorts interleaved). `allow(item, t)` is
    an extra per-slot predicate (the seasonal window gate). An item whose shoot
    would violate the rules at the current slot is deferred and retried at
    every later slot of this run; whatever is still deferred when the run's
    slots are exhausted is dropped. Returns (assigned, dropped).
    """
    min_days = int(rules.get("min_days_apart", 7))
    max_win = int(rules.get("max_per_window", 2))
    window = int(rules.get("window_days", 30))
    by_shoot: dict[str, list[datetime]] = {k: list(v) for k, v in existing.items()}
    queue = list(items)
    deferred: list = []
    assigned: list[tuple[object, datetime]] = []

    def fits(item, t) -> bool:
        if allow is not None and not allow(item, t):
            return False
        shoot = shoot_of(item)
        return shoot is None or slot_allowed(t, by_shoot.get(shoot, []), min_days, max_win, window)

    tail = timedelta(days=horizon_days if horizon_days is not None else window)
    horizon: datetime | None = None
    last: datetime | None = None
    while queue or (deferred and horizon is not None and scheduler.peek() <= horizon):
        t = scheduler.peek()
        chosen = next((d for d in deferred if fits(d, t)), None)
        if chosen is not None:
            deferred.remove(chosen)
        elif queue:
            chosen = queue.pop(0)
            if not fits(chosen, t):
                deferred.append(chosen)
                if not queue and horizon is None:
                    horizon = (last or t) + tail
                continue
        else:
            scheduler.take(t)  # tail slot nobody can use; move on
            continue
        scheduler.take(t)
        if not queue:
            horizon = horizon or t + timedelta(days=window)
        assigned.append((chosen, t))
        shoot = shoot_of(chosen)
        if shoot is not None:
            by_shoot.setdefault(shoot, []).append(t)
    dropped = [(d, shoot_of(d)) for d in deferred]
    return assigned, dropped
