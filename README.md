# Utilyze Automation Platform

Headless workers that research VC investors and grant/pitch opportunities with an LLM,
validate the result against a strict JSON schema, and write it back into a Google Sheet —
filling only worker-owned cells, never touching human-owned or formula columns, and never
sending or applying to anything. A separate `--publish` step pushes firm-level (non-personal)
data to a Wix CMS collection.

- **`vc_research`** — Investors tab. Researches a firm and recommends an outreach channel
  (who to contact and how) — it does not draft or send anything.
- **`grants`** — Grants / Pitches tab. Research-only: eligibility, fit score, requirements,
  draft application outline. No send step.

## The queue lives in the spreadsheet

This is the central design fact. Column **Q** (`Deadline Formula`) on the Investors tab
holds, per row:

```
=IF(C3="","",
  IF(COUNTBLANK(D3:O3)>0,"PULL VC DATA",
    IF(R3="","PULL VC DATA",
      IF(TODAY()-R3>90,"STALE","CURRENT"))))
```

`PULL VC DATA` / `STALE` → process. `CURRENT` / blank → skip. Python **reads** that
decision and never writes column Q. When the worker fills D–O and stamps `Last Checked`
(column R), the formula recalculates and the row leaves the queue on its own.

Two invariants keep that working, and both are enforced by tests:

**The sentinel rule.** `COUNTBLANK(D3:O3)` can't tell "never researched" from "researched,
doesn't exist" — and most firms have no public phone, Twitter, or newsletter. So when the
LLM returns null for anything mapped into D–O, the worker writes a sentinel value
(an em-dash, `—`) rather than leaving the cell blank. Without this, those rows would
re-research on every run, forever, at cost. A cell holding the sentinel counts as filled
and isn't rewritten unless `--force-refresh`. When reading a cell as *input* (the Website
hint), the sentinel is treated as blank so it's never fed back to the model or fetched as
a URL.

**The D:O column range.** The formula scans a fixed range, so any column inserted between
D and O silently changes queue semantics. All bot-owned columns (`Bot_Status`,
`Confidence`, `Source_URLs`, `Research_Notes`, `Recommended_Channel`) are appended to the
right of every used column. `assert_no_owned_column_in_scanned_range()` fails the run if
one ever lands inside D:O.

`Bot_Status` is an **outcome log**, not queue state — `Needs Review` or `Error` only.
Error rows are held back until `--requeue`.

### Investors tab layout

| Col | Header | Owner |
|-----|--------|-------|
| A | `Status` | Human — never written |
| B | `Contact Date` | Human — never written |
| C | `Name` | Human (research input) |
| D–L | `Website` … `Application Link` | Worker |
| M–O | `LinkedIn`, `Twitter`, `Newsletter Yes/No` | Worker |
| P | `Category` | Worker — constrained to `Investor`/`Accelerator`/`Grant`/`Pitch`/`Research` |
| Q | `Deadline Formula` | Formula — the queue, never written |
| R | `Last Checked` | Worker (note the space) |
| S | `Deadline Status` | Formula — never written |

`Last Checked`, `Deadline Formula`, and `Deadline Status` must already exist; the worker
refuses to auto-create them. Creating a duplicate `Last Checked` would leave the formula's
own R cell blank and re-queue every row forever.

**The Grants / Pitches tab has no queue formula.** That worker keeps the older
blank-column-scanning queue logic, and the sentinel rule does not apply to it — without a
COUNTBLANK formula there's nothing to satisfy, and a sentinel would only add noise.

## Repository layout

```
main.py                    CLI (--worker, --batch, --force-refresh, --requeue, --dry-run, --publish)
framework/                 Generic runner, Worker contract, validation, logging
workers/vc_research/       Schema, column map, routing, prompt.md (Investors tab)
workers/grants/            Schema, column map, routing, prompt.md (Grants / Pitches tab)
sheets/client.py           Header-resolved Google Sheets I/O (gspread)
llm/client.py              Anthropic wrapper: research() with web search, extract() without
llm/fetch.py               Plain-HTTP page fetch for the cheap research path
wix/                       Save Data Item client + allowlisted publisher
config/                    Env-driven settings + company context blurb
tests/                     Unit tests + fakes (no network calls)
```

## Setup

1. **Google Cloud** — enable the Sheets API, create a service account, download its JSON
   key, point `GOOGLE_APPLICATION_CREDENTIALS` at it.
2. **Share the sheet** with the service-account email as **Editor**.
3. **`SHEET_ID`** — the long string in the sheet URL between `/d/` and `/edit`.
4. **`ANTHROPIC_API_KEY`** — research calls use `claude-sonnet-5` by default.
5. **Wix (only for `--publish`)** — `WIX_API_KEY`, `WIX_SITE_ID`, `WIX_COLLECTION_ID`.

Copy `.env.example` to `.env` and fill it in. `.env` and `*.json` are gitignored.

## Running

```sh
pip install -r requirements-dev.txt

python main.py --worker vc_research --dry-run --batch 1   # preview, no writes
python main.py --worker vc_research                        # real run, BATCH_SIZE rows
python main.py --worker vc_research --requeue              # include rows marked Error
python main.py --worker vc_research --force-refresh        # ignore the queue column
python main.py --worker grants --dry-run
python main.py --worker vc_research --publish --dry-run    # preview the Wix push
```

`--dry-run` still calls the LLM (so you see real proposed content) but writes nothing to
the sheet or to Wix.

**Backfill.** Thousands of rows currently read `PULL VC DATA` because their D–O fields
are empty. `BATCH_SIZE` defaults to 250, so the first several weekly runs are the big,
expensive ones and everything after that is small — only rows that went stale past 90
days or were newly added. 250 also keeps a run inside the 6-hour GitHub Actions job limit.
Once the backlog clears, drop the cron back down (see the TODO in
`.github/workflows/worker.yml`) — a row only goes STALE after 90 days regardless of how
often the workflow fires, so running weekly forever buys nothing once there's no backlog
left to clear.

**Cost.** `FETCH_FIRST=true` (default) fetches a row's known website over plain HTTP and
asks the model to extract from that text — no web-search tool calls. It only falls back to
the search-enabled call when there's no usable website or the extraction came back too
thin. Set `FETCH_FIRST=false` to always use full research.

**Pre-filled cells are never re-researched.** If a human (or an earlier pass) already put
a real value in a business column — `City`, `Stage`, whatever — `build_prompt()` tells the
model exactly that in an "Already known for this row" section and instructs it to copy the
value unchanged rather than spend a search on it. A cell already holding the sentinel
(`—`) is surfaced the same way, but as "confirmed not publicly available — return null,
don't search again." Either way, the model is only asked to actually research what's
genuinely still blank. See `VCResearchWorker._known_fields_block()`.

**Industry Focus, Category, and Stage are constrained vocabularies, not free text.**
They answer three different questions about a row:

- `Industry Focus` — **which sector**: one of `Generalist`, `Climate`, `Biotech`,
  `Utilities`, `Water`. `Generalist` is the catch-all for regular/general tech investing.
- `Category` — **what kind of opportunity** the row is, now that Grants / Pitches /
  research rows live on the same tab as VC firms: one of `Investor`, `Accelerator`,
  `Grant`, `Pitch`, `Research`. `Investor` is the catch-all/default — this tab's
  original, still-dominant row type.
- `Stage` — only ever `Pre Seed` or blank; every other stage (`Seed`, `Series A`, ...) is
  recorded as blank rather than as free text, since Utilyze only cares whether a firm
  invests at pre-seed.

All three are enforced in `workers/vc_research/schema.py` (`VCResearchResult`'s
`industry_focus`/`category`/`stage` validators) rather than trusted from the prompt
alone — an off-list value falls back to the catch-all, and a phrasing variant like
`"bio-tech"`, `"pitch competition"`, or `"pre-seed"` is normalized to the canonical
spelling. Like any other business column, a cell a human (or an earlier pass) already
filled is left untouched unless `--force-refresh`.

**Cheap rechecks.** A row that goes `STALE` (past 90 days, `Worker.wants_recheck()`) does
**not** get the full research treatment again — most fields (contact info, focus, stage,
socials) rarely change once found. Instead it runs a narrow, much shorter prompt
(`recheck_prompt.md`) that only re-verifies `Deadline`/`Application Link`, via a separate,
smaller schema (`VCRecheckResult`). Routing is left exactly as it was from the original
research pass — a recheck doesn't re-derive the contact picture routing depends on. A
recheck that can't confirm a field returns null for it, which is treated as
"couldn't verify," not "confirmed gone" — it never blanks out a value a full research pass
already found. Only a brand-new row (`PULL VC DATA`) gets the full, expensive path.

## Publishing to Wix

`--publish` is a separate step and never runs research. It pushes rows whose queue column
reads `CURRENT` — i.e. fully researched and fresh — to a Wix CMS collection via
[Bulk Save Data Items](https://dev.wix.com/docs/api-reference/business-solutions/cms/data-items/bulk-save-data-items)
(`POST /data/v2/bulk/items/save`, up to 1000 items per call), which upserts on each
item's ID. The item ID is a normalized slug of the firm name, so re-publishing updates
rather than duplicating.

Only these sheet columns are ever read: `Name`, `Website`, `Industry Focus`, `Stage`,
`City`, `State/Country`, `Application Link`, `Deadline`, `Deadline Status` (`wix/publish.py`'s
`SOURCE_FIELDS`). `Email`, `Phone`, `Contact Date`, `Status`, and every draft field are on an
explicit forbidden list and never touched. Sheet headers aren't valid Wix field keys as-is
(e.g. `State/Country`), so each is mapped to a camelCase key (`stateCountry`) rather than
passed through — `ALLOWED_WIX_KEYS` is the corresponding allowlist on the output side, and
`build_item()` refuses to emit anything not on it. It's allowlists on both ends, so a new
sheet column is excluded by default and has to be mapped deliberately, and a test fails if a
disallowed field ever reaches the payload.

The raw `Deadline` cell (a date, "Rolling", or blank) is never published as-is — it can't
sort correctly next to real dates on the Wix site, and never expires on its own.
`wix/deadline.py` expands it into `deadlineLabel`, `deadlineDate`, `deadlineSort` (an ISO
date, anchored to 2099-12-31 for rolling/unknown and 1900-01-01 for passed, so a date-sort
in Wix puts rolling last and passed first), `isRolling`, `daysLeft`, and a normalized
`deadlineStatus` (`ROLLING` / `OPEN` / `CLOSING SOON` within 14 days / `PASSED` / `UNKNOWN`).
`Deadline Status` can independently signal `ROLLING` even when the raw `Deadline` text
doesn't obviously say so.

## Testing

```sh
pytest
```

108 tests, all against in-memory fakes — no network, no credentials. They cover the sentinel
rule, queue selection from column Q, the D:O range invariant, the Wix allowlist and
publish idempotency, channel routing, the fetch-first fallback chain, the known-fields skip-list, the cheap recheck
path, and schema
validation/retry.

## Scheduling

`.github/workflows/worker.yml` runs weekly (09:00 UTC every Monday, temporarily bumped up
from biweekly to clear the current backlog faster — see the TODO comment in that file) for
both workers, plus `workflow_dispatch` for manual runs with all the same flags. Required
repo secrets: `ANTHROPIC_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (base64 of the key
file), `SHEET_ID`.

## Design notes

- **Header resolution is always by name**, never by fixed index.
- **Never-write guards are structural**: a worker's `column_map` cannot target a
  human-owned, formula, or runner-managed column (raises at construction), and the runner
  re-checks every write payload before it goes out.
- **Retry**: an unparseable LLM response is retried up to 2× with a corrective nudge; after
  3 failures the row is marked `Error` with the reason in `Research_Notes` and no business
  columns are touched.
- **Confidence gate**: results below `CONFIDENCE_MIN` (0.65) are still written but flagged
  with a `[LOW CONFIDENCE: x.xx]` prefix on `Research_Notes`.
- **Adding a worker**: implement `Worker` in `workers/<name>/`, register it in
  `main.py`'s `WORKER_REGISTRY`. Nothing in `framework/` changes.
