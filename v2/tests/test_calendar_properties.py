"""Property tests for the calendar module (C-02): statements that must hold for any input, not just the worked examples.

Hypothesis generates the stamps, the timezones (UTC, Tokyo, New York, two fixed offsets) and the days. The seed is
fixed in tests/conftest.py, so a failure repeats; the failing example is printed. Most moments fall in a five-week
window around the New York daylight-saving change on 2026-11-01, so that many items share a day and the day
boundaries are exercised often. Every test names the rules it checks in its docstring.
This file is owned by the orchestrator.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from briefing2.core.calendar import (
    DayBatch,
    Stamp,
    assign_day,
    batches_before,
    day_range,
    keys_on,
    local_day,
    partition_by_day,
)

ZONES: list[tzinfo] = [
    UTC,
    ZoneInfo("Asia/Tokyo"),
    ZoneInfo("America/New_York"),
    timezone(timedelta(hours=5, minutes=30)),
    timezone(timedelta(hours=-3)),
]
zones = st.sampled_from(ZONES)

# Moments: mostly the dense window, sometimes anywhere between 2020 and 2030. All are timezone-aware.
_dense = st.datetimes(
    min_value=datetime(2026, 10, 25),
    max_value=datetime(2026, 11, 29),
    timezones=zones,
    allow_imaginary=False,
)
_wide = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
    timezones=zones,
    allow_imaginary=False,
)
moments = st.one_of(_dense, _dense, _dense, _wide)

dense_days = st.dates(min_value=date(2026, 10, 28), max_value=date(2026, 11, 26))
wide_days = st.dates(min_value=date(2020, 1, 1), max_value=date(2030, 12, 31))

old_keys = st.sampled_from(["a", "b", "c", "d", "e", "f"])  # few keys, so repeats happen
new_keys = st.sampled_from(["u", "v", "w", "x", "y", "z"])  # never clash with old_keys


def stamps_with(keys: st.SearchStrategy[str]) -> st.SearchStrategy[Stamp]:
    return st.builds(Stamp, key=keys, published=st.none() | moments, fetched_at=moments)


stamp_lists = st.lists(stamps_with(old_keys), max_size=12)  # may repeat a key
distinct_stamp_lists = st.lists(stamps_with(old_keys), max_size=6, unique_by=lambda s: s.key)
batch_lists = st.lists(
    st.builds(DayBatch, day=dense_days, keys=st.lists(old_keys, min_size=1, max_size=3).map(tuple)),
    max_size=8,
)


def moment_of(item: Stamp) -> datetime:
    return item.published if item.published is not None else item.fetched_at


def first_by_key(items: list[Stamp]) -> dict[str, Stamp]:
    first: dict[str, Stamp] = {}
    for item in items:
        first.setdefault(item.key, item)
    return first


@given(items=stamp_lists, tz=zones)
def test_every_key_lands_once_on_its_assigned_day(items: list[Stamp], tz: tzinfo) -> None:
    """CAL-06, CAL-08: each distinct key is in exactly one batch: the day assigned to its first occurrence."""
    placed = [(batch.day, key) for batch in partition_by_day(items, tz) for key in batch.keys]
    keys = [key for _, key in placed]
    assert len(keys) == len(set(keys))
    assert set(keys) == {item.key for item in items}
    first = first_by_key(items)
    for day, key in placed:
        assert day == assign_day(first[key], tz)


@given(items=stamp_lists, tz=zones)
def test_days_ascend_and_keys_are_ordered_by_moment_then_key(items: list[Stamp], tz: tzinfo) -> None:
    """CAL-07: days strictly ascend, no batch is empty, and inside a batch keys follow (moment, key)."""
    batches = partition_by_day(items, tz)
    days = [batch.day for batch in batches]
    assert days == sorted(set(days))
    first = first_by_key(items)
    for batch in batches:
        assert batch.keys
        order = [(moment_of(first[key]), key) for key in batch.keys]
        assert order == sorted(order)


@given(items=distinct_stamp_lists, tz=zones, data=st.data())
def test_input_order_does_not_matter_for_distinct_keys(items: list[Stamp], tz: tzinfo, data: st.DataObject) -> None:
    """CAL-10: when no key repeats, any reordering of the input gives the same result."""
    shuffled = data.draw(st.permutations(items))
    assert partition_by_day(shuffled, tz) == partition_by_day(items, tz)


@given(a=wide_days, b=wide_days)
def test_day_range_has_no_gaps(a: date, b: date) -> None:
    """CAL-11: from start to end inclusive, one date per day, oldest first."""
    start, end = min(a, b), max(a, b)
    days = day_range(start, end)
    assert days[0] == start
    assert days[-1] == end
    assert len(days) == (end - start).days + 1
    assert all(later - earlier == timedelta(days=1) for earlier, later in itertools.pairwise(days))


@given(a=wide_days, b=wide_days)
def test_day_range_refuses_a_start_after_the_end(a: date, b: date) -> None:
    """CAL-11: a reversed range raises ValueError mentioning "start"."""
    assume(a != b)
    with pytest.raises(ValueError, match="start"):
        day_range(max(a, b), min(a, b))


@given(batches=batch_lists, day=dense_days)
def test_batches_before_keeps_exactly_the_earlier_days_in_order(batches: list[DayBatch], day: date) -> None:
    """CAL-12: every kept batch is strictly earlier, every dropped one is not, and the order is the original order."""
    kept = batches_before(batches, day)
    assert all(batch.day < day for batch in kept)
    assert [batch for batch in batches if batch.day >= day] == [batch for batch in batches if batch not in kept]
    assert list(kept) == [batch for batch in batches if batch in kept]


@given(items=stamp_lists, extra=st.lists(stamps_with(new_keys), max_size=6), day=dense_days, tz=zones)
def test_items_from_day_d_or_later_cannot_change_what_before_d_sees(
    items: list[Stamp], extra: list[Stamp], day: date, tz: tzinfo
) -> None:
    """CAL-14: no look-ahead. Adding new items that belong to day D or later leaves the batches before D unchanged."""
    late = [item for item in extra if assign_day(item, tz) >= day]
    expected = batches_before(partition_by_day(items, tz), day)
    assert batches_before(partition_by_day([*items, *late], tz), day) == expected
    assert batches_before(partition_by_day([*late, *items], tz), day) == expected


@given(items=stamp_lists, tz=zones, day=dense_days)
def test_functions_are_repeatable_and_leave_their_input_alone(items: list[Stamp], tz: tzinfo, day: date) -> None:
    """CAL-15: a second call gives an equal result, and no argument is changed."""
    snapshot = list(items)
    batches = partition_by_day(items, tz)
    assert partition_by_day(items, tz) == batches
    assert items == snapshot
    batch_snapshot = list(batches)
    assert batches_before(batches, day) == batches_before(batches, day)
    assert keys_on(batches, day) == keys_on(batches, day)
    assert list(batches) == batch_snapshot
    for item in items:
        assert assign_day(item, tz) == assign_day(item, tz)
        assert local_day(moment_of(item), tz) == local_day(moment_of(item), tz)
