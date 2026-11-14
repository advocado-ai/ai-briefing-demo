"""Offline tests for harvest.py: canned feed bytes only, no network, no files outside pytest's temporary folder.

Each test names the behaviour (H-1 to H-10, listed in README.md) it checks.
Run from the repository root:  pytest tools/harvest -q
"""

from __future__ import annotations

from datetime import UTC, datetime

from harvest import (
    FeedResult,
    Item,
    clean,
    expected_empty,
    feeds_at_risk,
    iso,
    item_id,
    last_success,
    merge_new,
    parse_feed,
)

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)
SOURCE = {"name": "Example Blog", "hint": "models"}

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Example</title><lastBuildDate>Thu, 12 Nov 2026 08:00:00 GMT</lastBuildDate>
<item><title>First &amp; best</title><link>https://example.com/a</link>
  <description>&lt;p&gt;A &lt;b&gt;new&lt;/b&gt; agent  framework.&lt;/p&gt;</description>
  <pubDate>Wed, 11 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>Second</title><link>https://example.com/b</link><description>Retrieval tricks.</description>
  <pubDate>Mon, 09 Nov 2026 23:30:00 GMT</pubDate></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom example</title><updated>2026-11-12T07:00:00Z</updated>
<entry><title>Release 1.2</title><link href="https://example.com/r12"/><updated>2026-11-11T10:00:00Z</updated>
  <content type="html">&lt;p&gt;Fixes.&lt;/p&gt;</content></entry>
</feed>"""

UNDATED = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>No date</title><link>https://example.com/nodate</link><description>Text.</description></item>
</channel></rss>"""

OLD = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Ancient</title><link>https://example.com/old</link><description>Text.</description>
  <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
<item><title>Recent</title><link>https://example.com/new</link><description>Text.</description>
  <pubDate>Wed, 11 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>"""

DUPES = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>One</title><link>https://example.com/x</link><description>a</description>
  <pubDate>Wed, 11 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>One again</title><link>https://example.com/x</link><description>b</description>
  <pubDate>Wed, 11 Nov 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def item(link: str, title: str = "t") -> Item:
    return Item(id=item_id(link), source="s", hint=None, title=title, link=link, abstract="", published=None, first_seen_at=iso(NOW))


def test_h1_rss_fields_are_parsed_and_markup_is_cleaned() -> None:
    """H-1: an RSS item keeps title, link, cleaned abstract, UTC publish time, source, hint and the first-seen time."""
    result = parse_feed(RSS, SOURCE, NOW, since_days=30)
    first = result.items[0]
    assert (first.title, first.link) == ("First & best", "https://example.com/a")
    assert first.abstract == "A new agent framework."
    assert first.published == "2026-11-11T08:00:00Z"
    assert (first.source, first.hint, first.first_seen_at) == ("Example Blog", "models", "2026-11-12T12:00:00Z")
    assert first.id == item_id("https://example.com/a")
    assert result.entries_seen == 2 and result.build_date == "2026-11-12T08:00:00Z"
    assert result.oldest_published == "2026-11-09T23:30:00Z"


def test_h1_atom_feed_uses_updated_and_content() -> None:
    """H-1: an Atom entry without a summary falls back to its content and its updated time."""
    (only,) = parse_feed(ATOM, SOURCE, NOW, since_days=30).items
    assert (only.link, only.abstract, only.published) == ("https://example.com/r12", "Fixes.", "2026-11-11T10:00:00Z")


def test_h2_undated_item_is_kept_with_no_published_time() -> None:
    """H-2: an item with no date is kept, with published None, whatever the window is."""
    (only,) = parse_feed(UNDATED, SOURCE, NOW, since_days=1).items
    assert only.published is None and only.first_seen_at == "2026-11-12T12:00:00Z"


def test_h3_keyword_filter_keeps_only_matching_items() -> None:
    """H-3: with keywords in the source, an item is kept only if its title or abstract contains one (case-blind)."""
    kept = parse_feed(RSS, {**SOURCE, "keywords": ["AGENT"]}, NOW, 30)
    assert [i.link for i in kept.items] == ["https://example.com/a"]
    assert kept.entries_seen == 2  # the count is taken before the filter
    assert parse_feed(RSS, {**SOURCE, "keywords": ["zebra"]}, NOW, 30).items == ()


def test_h4_window_drops_items_older_than_since_days() -> None:
    """H-4: an item published before now minus since_days is dropped; a recent one stays."""
    result = parse_feed(OLD, SOURCE, NOW, since_days=30)
    assert [i.link for i in result.items] == ["https://example.com/new"]
    assert result.entries_seen == 2 and result.oldest_published == "2024-01-01T00:00:00Z"


def test_h5_a_link_repeated_inside_one_feed_is_kept_once() -> None:
    """H-5: the first of two entries with the same link wins."""
    (only,) = parse_feed(DUPES, SOURCE, NOW, 30).items
    assert only.title == "One"


def test_h6_merge_skips_known_links_and_first_sighting_wins() -> None:
    """H-6: merge_new drops links already stored and links repeated in the batch; a second merge adds nothing."""
    batch = (item("https://example.com/a", "first"), item("https://example.com/a", "edited"), item("https://example.com/b"))
    fresh = merge_new({"https://example.com/b"}, batch)
    assert [(i.link, i.title) for i in fresh] == [("https://example.com/a", "first")]
    assert merge_new({i.link for i in fresh} | {"https://example.com/b"}, batch) == ()


def test_h7_malformed_bytes_give_an_empty_result_without_crashing() -> None:
    """H-7: junk bytes give no items and entries_seen 0."""
    for junk in (b"", b"not a feed at all", b"<html><body>Please enable JavaScript</body></html>"):
        assert parse_feed(junk, SOURCE, NOW, 30) == FeedResult(entries_seen=0, items=(), oldest_published=None, build_date=None)


def test_h7_a_valid_empty_feed_is_reported_as_zero_entries() -> None:
    """H-7: a feed with a channel and no items (as the arXiv feeds sometimes are) shows entries_seen 0, so the shell can warn."""
    empty = b'<?xml version="1.0"?><rss version="2.0"><channel><title>cs.AI updates</title></channel></rss>'
    assert parse_feed(empty, SOURCE, NOW, 30).entries_seen == 0


def test_h8_feeds_at_risk_are_those_whose_oldest_entry_is_newer_than_the_last_good_run() -> None:
    """H-8: only a feed that no longer reaches back to the previous run may have dropped items."""
    since = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
    results = {
        "fast": FeedResult(10, (), "2026-11-10T08:00:00Z", None),  # oldest is after `since`: items may be lost
        "deep": FeedResult(100, (), "2026-06-01T00:00:00Z", None),  # reaches back past `since`: nothing lost
        "empty": FeedResult(0, (), None, None),
        "undated": FeedResult(3, (), None, None),
    }
    assert feeds_at_risk(results, since) == ("fast",)


def test_h9_last_success_ignores_runs_that_reached_no_feed() -> None:
    """H-9: the last successful run is the latest run line with at least one feed ok; feed lines and failed runs do not count."""
    lines = [
        {"kind": "run", "run_at": "2026-11-10T09:00:00Z", "feeds_ok": 20},
        {"kind": "feed", "run_at": "2026-11-11T09:00:00Z", "source": "x"},
        {"kind": "run", "run_at": "2026-11-11T09:00:00Z", "feeds_ok": 0},
    ]
    assert last_success(lines) == datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
    assert last_success([]) is None


def test_h10_the_id_is_stable_and_depends_only_on_the_link() -> None:
    """H-10: the same link always gives the same ten-character id; different links differ."""
    assert item_id("https://example.com/a") == item_id("https://example.com/a")
    assert item_id("https://example.com/a") != item_id("https://example.com/b")
    assert len(item_id("https://example.com/a")) == 10


def test_clean_strips_script_and_style_and_unescapes() -> None:
    """H-1 (cleaning): scripts and styles disappear with their content, tags go, entities are unescaped, spaces collapse."""
    assert clean("<style>p{}</style><p>Fish &amp;   chips</p><script>x()</script>") == "Fish & chips"


ARXIV = b"""<?xml version='1.0' encoding='UTF-8'?>
<rss xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:atom="http://www.w3.org/2005/Atom" version="2.0">
<channel><title>cs.AI updates on arXiv.org</title><lastBuildDate>Fri, 13 Nov 2026 05:00:00 +0000</lastBuildDate>
<skipDays><day>Sunday</day><day>Saturday</day></skipDays>
<item><title>A new agent benchmark</title><link>https://arxiv.org/abs/2611.00001</link>
<description>arXiv:2611.00001v1 Announce Type: new
Abstract: We build a benchmark for agent evaluation.</description>
<pubDate>Fri, 13 Nov 2026 00:00:00 -0500</pubDate><arxiv:announce_type>new</arxiv:announce_type></item>
<item><title>Cross-listed retrieval paper</title><link>https://arxiv.org/abs/2611.00002</link>
<description>arXiv:2611.00002v2 Announce Type: cross
Abstract: Retrieval for language models.</description>
<pubDate>Fri, 13 Nov 2026 00:00:00 -0500</pubDate><arxiv:announce_type>cross</arxiv:announce_type></item>
<item><title>Mass concentration</title><link>https://arxiv.org/abs/2203.01250</link>
<description>arXiv:2203.01250v3 Announce Type: replace-cross
Abstract: We consider first order problems with language model flavour.</description>
<pubDate>Fri, 13 Nov 2026 00:00:00 -0500</pubDate><arxiv:announce_type>replace-cross</arxiv:announce_type></item>
<item><title>An updated reasoning paper</title><link>https://arxiv.org/abs/2601.00003</link>
<description>arXiv:2601.00003v4 Announce Type: replace
Abstract: Reasoning with tools.</description>
<pubDate>Fri, 13 Nov 2026 00:00:00 -0500</pubDate><arxiv:announce_type>replace</arxiv:announce_type></item>
</channel></rss>"""

ARXIV_NO_ELEMENT = ARXIV.replace(b"<arxiv:announce_type>replace</arxiv:announce_type>", b"").replace(
    b"<arxiv:announce_type>new</arxiv:announce_type>", b""
)

SATURDAY_EMPTY = b"""<?xml version='1.0'?><rss version="2.0"><channel><title>cs.AI updates on arXiv.org</title>
<lastBuildDate>Sat, 14 Nov 2026 05:00:00 +0000</lastBuildDate><skipDays><day>Sunday</day><day>Saturday</day></skipDays></channel></rss>"""


def test_h11_arxiv_replacements_are_dropped_and_new_and_cross_are_kept() -> None:
    """H-11: of arXiv's announce types only `new` and `cross` are kept; `replace` and `replace-cross` re-announce old papers."""
    result = parse_feed(ARXIV, {"name": "arXiv cs.AI"}, NOW, 30)
    assert [(i.link, i.announce_type) for i in result.items] == [
        ("https://arxiv.org/abs/2611.00001", "new"),
        ("https://arxiv.org/abs/2611.00002", "cross"),
    ]
    assert result.entries_seen == 4  # counted before the drop


def test_h11_announce_type_is_read_from_the_text_when_the_element_is_missing() -> None:
    """H-11: without the element, the type in the description's own prefix decides (the replace is still dropped)."""
    result = parse_feed(ARXIV_NO_ELEMENT, {"name": "arXiv cs.AI"}, NOW, 30)
    assert [i.announce_type for i in result.items] == ["new", "cross"]


def test_h11_other_feeds_have_no_announce_type() -> None:
    """H-11: an item from a non-arXiv feed has announce_type None and is never dropped for it."""
    assert all(i.announce_type is None for i in parse_feed(RSS, SOURCE, NOW, 30).items)


def test_h12_arxiv_prefix_is_removed_from_the_abstract() -> None:
    """H-12: the abstract starts at the text after `Abstract:`, with no arXiv id and no announce-type words."""
    first = parse_feed(ARXIV, {"name": "arXiv cs.AI"}, NOW, 30).items[0]
    assert first.abstract == "We build a benchmark for agent evaluation."


def test_h12_a_normal_abstract_is_not_touched() -> None:
    """H-12: text that does not start with arXiv's prefix is left as it is."""
    assert parse_feed(RSS, SOURCE, NOW, 30).items[1].abstract == "Retrieval tricks."


def test_h12_keywords_see_the_abstract_after_the_prefix() -> None:
    """H-12 with H-3: the keyword filter applies to the new items (agent, retrieval) and finds nothing in the dropped replacements."""
    result = parse_feed(ARXIV, {"name": "arXiv cs.AI", "keywords": ["flavour"]}, NOW, 30)
    assert [i.link for i in result.items] == []  # the only `flavour` is in a replacement, which is dropped
    kept = parse_feed(ARXIV, {"name": "arXiv cs.AI", "keywords": ["retrieval"]}, NOW, 30)
    assert [i.link for i in kept.items] == ["https://arxiv.org/abs/2611.00002"]


def test_h13_skip_days_are_read_from_the_feed() -> None:
    """H-13: the feed's own <skipDays> names come back, sorted; a feed without the element gives none."""
    assert parse_feed(ARXIV, {"name": "x"}, NOW, 30).skip_days == ("Saturday", "Sunday")
    assert parse_feed(RSS, SOURCE, NOW, 30).skip_days == ()


def test_h13_an_empty_feed_built_on_a_skipped_day_is_expected() -> None:
    """H-13: empty and built on a Saturday that the feed skips: expected. Empty on a Thursday: not expected. Items present: not empty."""
    saturday = parse_feed(SATURDAY_EMPTY, {"name": "x"}, NOW, 30)
    assert saturday.entries_seen == 0 and expected_empty(saturday)
    thursday = FeedResult(0, (), None, "2026-11-12T05:00:00Z", ("Saturday", "Sunday"))
    assert not expected_empty(thursday)
    assert not expected_empty(FeedResult(0, (), None, None, ("Saturday",)))  # no build date: cannot say
    assert not expected_empty(parse_feed(ARXIV, {"name": "x"}, NOW, 30))
    # the weekday must be exact, not a neighbour: 2026-11-13 is a Friday, 2026-11-14 a Saturday
    assert expected_empty(FeedResult(0, (), None, "2026-11-14T05:00:00Z", ("Saturday",)))
    assert not expected_empty(FeedResult(0, (), None, "2026-11-14T05:00:00Z", ("Sunday",)))
    assert not expected_empty(FeedResult(0, (), None, "2026-11-13T05:00:00Z", ("Saturday",)))
