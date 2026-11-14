# Daily feed harvester

Keeps the articles the feeds publish while the new system is still being built, so no day is lost. It fetches every RSS and Atom feed in
`config/sources.yaml` once, and appends each item it has not seen before to a local file. Nothing else: no keys, no database, no model.
Embedding and scoring happen later, in the back-dated run (REQ-51), and embedding is cheap (about 3 seconds per 100 abstracts), so
collecting the raw text now loses nothing.

It is interim tooling, not product code, so it lives outside `v2/`. Why it exists, and the measurements behind it, are in the plan
"H1: daily harvest" (2026-11-12).

## Use

From the repository root, in the conda environment `ai-briefing-harvest`
(`conda env create -f tools/harvest/environment.yml`, from the repo root):

```bash
python tools/harvest/harvest.py              # fetch every feed once, append what is new
python tools/harvest/harvest.py --status     # when did it last work, and how much arrived lately
pytest tools/harvest -q                      # offline tests
```

Options: `--since-days N` (default 30: keep items published in the last N days, plus undated ones, so the first run does not bank a
feed's whole history), `--no-notify`, `--data-dir`, `--sources`. Exit code 0 if at least one feed answered, 1 if none did.
Running it twice in a row is safe: the second run adds nothing. Running it by hand is always fine, whatever the timer does.

## Data (git-ignored: other publishers' text, kept on this machine only)

| File | One line per | Holds |
|---|---|---|
| `data/items.jsonl` | new item, appended once | `id` (provisional: the first 10 hex characters of SHA-1 of the link; the archive recomputes it from the canonical URL), `source`, `hint`, `title`, `link`, `abstract` (cleaned feed text, at most 5,000 characters, may be empty), `published` (UTC, or null), `first_seen_at` (UTC), `announce_type` (arXiv only: `new` or `cross`; null elsewhere) |
| `data/runs.jsonl` | feed per run, and one `run` line per run | per feed: `status` (ok, error, skipped), `http`, `entries_seen` (before any filter), `kept`, `new`, `oldest_published`, `build_date` (the feed's own "updated" time), `empty`, `expected_empty` (empty on a day the feed says it skips). Per run: `feeds_ok`, `feeds_failed`, `feeds_skipped`, `new_items`, `since_days` |

The first sighting of a link wins: a later edit of a title or abstract is ignored. Items with no abstract are kept on purpose (Hacker News
and GitHub items are real inputs to the pipeline). There is no minimum length.

## Behaviour (what the tests check)

| ID | Behaviour |
|---|---|
| H-1 | An item keeps title, link, cleaned abstract, UTC publish time, source, hint and first-seen time. Atom entries fall back to `content` and `updated` |
| H-2 | An item with no date is kept, with `published` null, whatever the window |
| H-3 | With `keywords` in the source, an item is kept only if its title or abstract contains one (case-blind); `entries_seen` is counted before the filter |
| H-4 | An item published before now minus `--since-days` is dropped |
| H-5 | A link repeated inside one feed is kept once (the first) |
| H-6 | `merge_new` drops links already stored and repeats inside the batch; a second merge adds nothing |
| H-7 | Junk bytes give an empty result without crashing; a valid feed with no items shows `entries_seen` 0, and the run prints a warning |
| H-8 | `feeds_at_risk`: a feed whose oldest listed entry is newer than the previous good run may have dropped items meanwhile. The run prints a warning naming it, and sends a desktop notification if `notify-send` exists |
| H-9 | The last successful run is the latest `run` line with at least one feed answering; feed lines and failed runs do not count |
| H-10 | The id depends only on the link |
| H-11 | arXiv: only announce types `new` and `cross` are kept. `replace` and `replace-cross` re-announce old papers under a new date, so they are not new articles. The type comes from the feed's `arxiv:announce_type` element, or from the description's own prefix if the element is missing. Other feeds are never affected |
| H-12 | arXiv: the prefix `arXiv:ID Announce Type: ... Abstract:` is removed from the abstract, before the keyword filter reads it |
| H-13 | An empty feed built on a day its own `<skipDays>` lists (arXiv: Saturday and Sunday) is expected: no warning, shown as `empty` in lower case, `expected_empty` true in the log |

## Known limits

- **The three scraped pages are skipped** (Langfuse Changelog, DeepLearning.AI The Batch and Courses): they have no feed.
  A later fetch can still read their current page; their history cannot be recovered.
- **Fast feeds hold only 2 to 3 days** (Wired AI, TechCrunch AI, AWS ML Blog, IBM Newsroom AI, measured 2026-11-12). If this machine is
  off longer than that, items from them are lost; the `feeds_at_risk` warning on the next run names the feeds.
- **arXiv publishes one batch a day.** Per arXiv's RSS pages (read 2026-11-13), feeds are rebuilt once a day at midnight Eastern (05:00 UTC),
  replace the previous batch, and are empty on Saturday and Sunday and on occasional holidays. The two daily slots therefore both fall
  inside a batch's 24 hours. The RSS has **no history**: a batch missed entirely cannot be recovered from it (the arXiv search API could,
  under its own terms of use, and is a separate decision). On 2026-11-12, a Thursday, both feeds were nevertheless empty; the cause is not known.
  `runs.jsonl` records `build_date` and `entries_seen` each run. `https://rss.arxiv.org/rss/cs.ai+cs.cl` would serve both categories as one
  feed with cross-listed papers once; it is not used yet because it has not been seen with items in it.
- The window is a guess at what the back-dated run needs; it is a flag because that is a design question for the backfill.
- Publisher text stays in the ignored data folder. Nothing is pushed anywhere. There is no backup of `data/`: copy it if that matters.
- `config/sources.yaml` is the feed list. If the source list moves into `v2/`, point `--sources` at it.

## The timer (systemd user units in `systemd/`)

Twice a day, 09:00 and 21:00 in the machine's local time (Asia/Taipei here). **If the laptop was off or asleep, it catches up on its own**,
in two ways that overlap on purpose: `Persistent=true` runs a missed slot as soon as the timer starts, and `OnStartupSec=3min` runs a few
minutes after the first login of the day. If every feed fails (the network is not up yet), the service retries every 2 minutes, at most 4
starts in 30 minutes. A long gap is announced by the run itself (H-8 warning, plus a desktop notification). A manual run always works.
Not decided for you: `loginctl enable-linger`, which would let the timer run while nobody is logged in. It is not needed for a laptop used
daily, so it stays off.

**Install** (these touch your home folder outside the repo, so they are yours to run), from the repository root:

```bash
mkdir -p ~/.config/systemd/user
cp tools/harvest/systemd/ai-briefing-harvest.service tools/harvest/systemd/ai-briefing-harvest.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now ai-briefing-harvest.timer
```

**Check it:**

```bash
systemctl --user list-timers ai-briefing-harvest.timer      # next and last run
systemctl --user start ai-briefing-harvest.service          # force one run now
journalctl --user -u ai-briefing-harvest.service -n 40      # what it printed
python tools/harvest/harvest.py --status                    # last good run and items per day
```

A forced run right after another adds 0 items. **Remove:**

```bash
systemctl --user disable --now ai-briefing-harvest.timer
systemctl --user clean --what=state ai-briefing-harvest.timer   # forget the catch-up timestamp
rm ~/.config/systemd/user/ai-briefing-harvest.{service,timer} && systemctl --user daemon-reload
```

The unit files use `%h` (your home folder), so they assume the repo at `~/ai-briefing-demo` and the environment at
`~/miniconda3/envs/ai-briefing-harvest`. If either moves, edit the three paths in the `.service` file.

**Not verified until it has run for real:** that a laptop suspended across a slot fires on resume; how long the network takes after login
on this machine; and that the desktop notification appears from a service (the user manager does carry the session bus address here).
The first week of `data/runs.jsonl` and the `journalctl` output will show.
