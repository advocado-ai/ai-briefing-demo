"""Calendar (C-02): which day each item belongs to, and what a given day is allowed to see.

Spec: the calendar spec, not published here (rules CAL-T1..T3 and CAL-01..16).
Pure functions only: no clock, no files, no network.
The two types below are finished and are pinned by tests. The six functions are the implementer's job.
"""

from collections.abc import Sequence
from datetime import date, datetime, timedelta, tzinfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Stamp(BaseModel):
    """What the calendar needs to know about one item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    published: AwareDatetime | None
    fetched_at: AwareDatetime


class DayBatch(BaseModel):
    """The items that belong to one day, in a stable order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    day: date
    keys: tuple[str, ...] = Field(min_length=1)


def local_day(moment: datetime, tz: tzinfo) -> date:
    """The calendar date of `moment` as seen in `tz` (CAL-01, CAL-02, CAL-16)."""
    if moment.utcoffset() is None:
        raise ValueError(f"moment must be timezone-aware, got {moment!r}")
    return moment.astimezone(tz).date()


def assign_day(stamp: Stamp, tz: tzinfo) -> date:
    """The day a stamp belongs to (CAL-03, CAL-04, CAL-05)."""
    fetched_day = local_day(stamp.fetched_at, tz)
    if stamp.published is None:
        return fetched_day
    return min(local_day(stamp.published, tz), fetched_day)


def moment_and_key(stamp: Stamp) -> tuple[datetime, str]:
    """The order of a stamp inside its day: its own moment, then its key (CAL-07, D-4)."""
    moment = stamp.fetched_at if stamp.published is None else stamp.published
    return (moment, stamp.key)


def partition_by_day(stamps: Sequence[Stamp], tz: tzinfo) -> tuple[DayBatch, ...]:
    """Group stamps into one batch per day, oldest day first (CAL-06 to CAL-10)."""
    first_seen: dict[str, Stamp] = {}
    for item in stamps:
        first_seen.setdefault(item.key, item)
    by_day: dict[date, list[Stamp]] = {}
    for item in first_seen.values():
        by_day.setdefault(assign_day(item, tz), []).append(item)
    return tuple(
        DayBatch(
            day=day,
            keys=tuple(item.key for item in sorted(by_day[day], key=moment_and_key)),
        )
        for day in sorted(by_day)
    )


def day_range(start: date, end: date) -> tuple[date, ...]:
    """Every date from `start` to `end` inclusive, oldest first (CAL-11)."""
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def batches_before(batches: Sequence[DayBatch], day: date) -> tuple[DayBatch, ...]:
    """The batches whose day is strictly earlier than `day`, in their original order (CAL-12)."""
    return tuple(batch for batch in batches if batch.day < day)


def keys_on(batches: Sequence[DayBatch], day: date) -> tuple[str, ...]:
    """The keys of the batch for exactly `day`, or an empty tuple (CAL-13)."""
    for batch in batches:
        if batch.day == day:
            return batch.keys
    return ()
