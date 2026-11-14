"""Example tests for the calendar module (C-02): one test per worked example in the calendar spec, not published here.

Every test names the rules it checks (CAL-nn) and the examples it covers (E-nn) in its docstring.
This file is owned by the orchestrator. The implementer does not change it: a test that looks wrong becomes a
test-change request in the report.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, tzinfo
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

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

TOKYO = ZoneInfo("Asia/Tokyo")
NY = ZoneInfo("America/New_York")
FETCHED = "2026-11-11T08:00:00Z"


def at(iso: str) -> datetime:
    """A timezone-aware moment from text such as 2026-11-10T16:30:00Z."""
    return datetime.fromisoformat(iso)


def stamp(key: str, published: str | None, fetched: str = FETCHED) -> Stamp:
    return Stamp(key=key, published=None if published is None else at(published), fetched_at=at(fetched))


# The input of examples E-15 to E-18, E-25 and E-31. Every stamp was fetched at FETCHED.
PARTITION_INPUT = (
    stamp("a", "2026-11-09T10:00:00Z"),
    stamp("b", "2026-11-10T22:00:00Z"),
    stamp("c", None),
    stamp("d", "2026-11-09T08:00:00Z"),
    stamp("e", "2026-11-12T10:00:00Z"),
    stamp("a", "2026-11-10T01:00:00Z"),
)
UTC_BATCHES = (
    DayBatch(day=date(2026, 11, 9), keys=("d", "a")),
    DayBatch(day=date(2026, 11, 10), keys=("b",)),
    DayBatch(day=date(2026, 11, 11), keys=("c", "e")),
)


# --- Single instants (section 6, first table) -----------------------------------------------------------------------

LOCAL_DAY_CASES = [
    pytest.param("2026-11-10T16:30:00Z", UTC, date(2026, 11, 10), id="E-01"),
    pytest.param("2026-11-10T16:30:00Z", TOKYO, date(2026, 11, 11), id="E-02"),
    pytest.param("2026-11-10T14:59:59Z", TOKYO, date(2026, 11, 10), id="E-03"),
    pytest.param("2026-11-10T15:00:00Z", TOKYO, date(2026, 11, 11), id="E-04"),
    pytest.param("2026-11-01T05:30:00Z", NY, date(2026, 11, 1), id="E-05"),
    pytest.param("2026-11-01T06:30:00Z", NY, date(2026, 11, 1), id="E-06"),
    pytest.param("2026-11-02T04:59:59Z", NY, date(2026, 11, 1), id="E-07"),
    pytest.param("2026-11-02T05:00:00Z", NY, date(2026, 11, 2), id="E-08"),
    pytest.param("2026-03-08T06:59:59Z", NY, date(2026, 3, 8), id="E-09"),
    pytest.param("2026-03-08T07:00:00Z", NY, date(2026, 3, 8), id="E-10"),
]


@pytest.mark.parametrize(("iso", "tz", "expected"), LOCAL_DAY_CASES)
def test_local_day_follows_local_midnight(iso: str, tz: tzinfo, expected: date) -> None:
    """CAL-01, CAL-16 (E-01 to E-10): the same instant can be two different days; daylight saving moves nothing."""
    assert local_day(at(iso), tz) == expected


def test_local_day_rejects_a_moment_without_timezone() -> None:
    """CAL-02 (E-30): a naive datetime is an error that says "timezone-aware"."""
    with pytest.raises(ValueError, match="timezone-aware"):
        local_day(datetime(2026, 11, 10, 16, 30), UTC)


# --- Assigning a day (fetched_at is FETCHED throughout) -------------------------------------------------------------


@pytest.mark.parametrize(
    ("published", "tz", "expected"),
    [
        pytest.param("2026-11-09T23:30:00Z", UTC, date(2026, 11, 9), id="E-11"),
        pytest.param("2026-11-09T23:30:00Z", TOKYO, date(2026, 11, 10), id="E-12"),
    ],
)
def test_assign_day_uses_the_published_day(published: str, tz: tzinfo, expected: date) -> None:
    """CAL-03 (E-11, E-12): a published item belongs to the local day it was published."""
    assert assign_day(stamp("k", published), tz) == expected


def test_assign_day_falls_back_to_the_fetch_day() -> None:
    """CAL-04 (E-13): an item with no published date belongs to the day it was fetched."""
    assert assign_day(stamp("k", None), UTC) == date(2026, 11, 11)


def test_assign_day_never_goes_past_the_fetch_day() -> None:
    """CAL-05 (E-14): a published date after the fetch is treated as the fetch day."""
    assert assign_day(stamp("k", "2026-11-12T10:00:00Z"), UTC) == date(2026, 11, 11)


# Fetched at 01:00 on the 11th in Tokyo, which is still the 10th in UTC.
FETCHED_TOKYO_MORNING = "2026-11-10T16:00:00Z"


@pytest.mark.parametrize(
    ("published", "expected"),
    [
        pytest.param(None, date(2026, 11, 11), id="E-32"),
        pytest.param("2026-11-11T10:00:00Z", date(2026, 11, 11), id="E-33"),
        pytest.param("2026-11-12T10:00:00Z", date(2026, 11, 11), id="E-34"),
    ],
)
def test_assign_day_compares_with_the_local_fetch_day(published: str | None, expected: date) -> None:
    """CAL-04, CAL-05 (E-32 to E-34): the fetch day is the day in the given timezone, not the UTC date."""
    assert assign_day(stamp("k", published, fetched=FETCHED_TOKYO_MORNING), TOKYO) == expected


# --- Partitioning ---------------------------------------------------------------------------------------------------


def test_partition_in_utc() -> None:
    """CAL-06, CAL-07, CAL-08 (E-15): one batch per day, ordered inside, the first duplicate wins."""
    assert partition_by_day(PARTITION_INPUT, UTC) == UTC_BATCHES


def test_partition_in_tokyo_moves_late_items_to_the_next_day() -> None:
    """CAL-01, CAL-06 (E-16): b, published 22:00 UTC on the 10th, is already the 11th in Tokyo; the 10th disappears."""
    assert partition_by_day(PARTITION_INPUT, TOKYO) == (
        DayBatch(day=date(2026, 11, 9), keys=("d", "a")),
        DayBatch(day=date(2026, 11, 11), keys=("b", "c", "e")),
    )


def test_partition_of_nothing_is_an_empty_tuple() -> None:
    """CAL-09 (E-17): no input gives an empty tuple, not a list and not None."""
    assert partition_by_day((), UTC) == ()


def test_partition_ignores_input_order() -> None:
    """CAL-10 (E-18): without repeated keys, reversing the input changes nothing."""
    without_repeat = PARTITION_INPUT[:-1]
    assert partition_by_day(tuple(reversed(without_repeat)), UTC) == UTC_BATCHES


# --- Day sequences and visibility -----------------------------------------------------------------------------------


def test_day_range_is_inclusive_and_gap_free() -> None:
    """CAL-11 (E-19): four days from the 8th to the 11th, oldest first."""
    assert day_range(date(2026, 11, 8), date(2026, 11, 11)) == (
        date(2026, 11, 8),
        date(2026, 11, 9),
        date(2026, 11, 10),
        date(2026, 11, 11),
    )


def test_day_range_of_one_day() -> None:
    """CAL-11 (E-20): start equal to end gives that one date."""
    assert day_range(date(2026, 11, 11), date(2026, 11, 11)) == (date(2026, 11, 11),)


def test_day_range_rejects_a_start_after_the_end() -> None:
    """CAL-11 (E-21): a reversed range is an error that mentions "start"."""
    with pytest.raises(ValueError, match="start"):
        day_range(date(2026, 11, 11), date(2026, 11, 8))


def test_batches_before_is_strictly_before() -> None:
    """CAL-12 (E-22): the batch of the 10th itself is not visible on the 10th."""
    assert batches_before(UTC_BATCHES, date(2026, 11, 10)) == (UTC_BATCHES[0],)


def test_keys_on_finds_the_batch_of_that_day() -> None:
    """CAL-13 (E-23)."""
    assert keys_on(UTC_BATCHES, date(2026, 11, 10)) == ("b",)


def test_keys_on_a_day_without_items_is_empty() -> None:
    """CAL-13 (E-24)."""
    assert keys_on(UTC_BATCHES, date(2026, 11, 8)) == ()


def test_a_later_item_does_not_change_what_an_earlier_day_sees() -> None:
    """CAL-14 (E-25): adding an item published on the 11th changes nothing before the 11th."""
    later = stamp("z", "2026-11-11T09:00:00Z")
    with_later = partition_by_day([*PARTITION_INPUT, later], UTC)
    assert batches_before(with_later, date(2026, 11, 11)) == batches_before(UTC_BATCHES, date(2026, 11, 11))


# --- Types and purity -----------------------------------------------------------------------------------------------


def test_stamp_needs_a_key() -> None:
    """CAL-T2 (E-26): an empty key is a validation error."""
    with pytest.raises(ValidationError):
        Stamp(key="", published=None, fetched_at=at(FETCHED))


@pytest.mark.parametrize(
    ("published", "fetched_at"),
    [
        pytest.param(datetime(2026, 11, 9, 10, 0), at(FETCHED), id="E-27-published"),
        pytest.param(None, datetime(2026, 11, 11, 8, 0), id="E-27-fetched"),
    ],
)
def test_stamp_needs_timezone_aware_moments(published: datetime | None, fetched_at: datetime) -> None:
    """CAL-T2 (E-27): a published or fetched time without a timezone is a validation error."""
    with pytest.raises(ValidationError):
        Stamp(key="a", published=published, fetched_at=fetched_at)


def test_stamp_is_immutable_and_rejects_unknown_fields() -> None:
    """CAL-T1 (E-28): assigning to a field raises; an extra field is refused."""
    item = stamp("a", None)
    with pytest.raises(ValidationError):
        item.__setattr__("key", "z")
    with pytest.raises(ValidationError):
        Stamp.model_validate({"key": "a", "published": None, "fetched_at": at(FETCHED), "extra": 1})


def test_day_batch_needs_keys_and_is_immutable() -> None:
    """CAL-T3 (E-29): an empty batch is refused, and a batch cannot be changed."""
    with pytest.raises(ValidationError):
        DayBatch(day=date(2026, 11, 9), keys=())
    with pytest.raises(ValidationError):
        UTC_BATCHES[0].__setattr__("day", date(2026, 11, 10))


def test_partition_is_repeatable_and_leaves_its_input_alone() -> None:
    """CAL-15 (E-31): two calls give equal results and the input list is unchanged."""
    stamps = list(PARTITION_INPUT)
    before = list(stamps)
    assert partition_by_day(stamps, UTC) == partition_by_day(stamps, UTC)
    assert stamps == before
