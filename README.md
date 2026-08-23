# Utilyze Automation Platform

Headless workers that research VC investors and grant/pitch opportunities with an LLM,
validate the result against a strict JSON schema, and write it back into a Google Sheet —
filling only worker-owned cells, never touching human-owned or formula columns, and never
sending or applying to anything. A separate `--publish` step pushes firm-level (non-personal)
data to a Wix CMS collection.

- **`vc_research`** — Investors tab. Researches a firm, recommends an outreach channel,
  drafts an intro email (optionally as a real Gmail draft).
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
LLM returns null for anything mapped into D–O, the worker writes the literal string
`None`. Without this, those rows would re-research on every run, forever, at cost. A cell
holding `None` counts as filled and isn't rewritten unless `--force-refresh`. When reading
a cell as *input* (the Website hint), `None` is treated as blank so it's never fed back to
the model or fetched as a URL.

**The D:O column range.** The formula scans a fixed range, so any column inserted between
D and O silently changes queue semantics. All bot-owned columns (`Bot_Status`,
`Confidence`, `Source_URLs`, `Research_Notes`, `Recommended_Channel`, `Draft_*`) are
appended to the right of every used column. `assert_no_owned_column_in_scanned_range()`
fails the run if one ever lands inside D:O.

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
| P | `Category` | Human — never written |
| Q | `Deadline Formula` | Formula — the queue, never written |
| R | `Last Checked` | Worker (note the space) |
| S | `Deadline Status` | Formula — never written |

`Last Checked`, `Deadline Formula`, and `Deadline Status` must already exist; the worker
refuses to auto-create them. Creating a duplicate `Last Checked` would leave the formula's
own R cell blank and re-queue every row forever.

**The Grants / Pitches tab has no queue formula.** That worker keeps the older
blank-column-scanning queue logic, and the sentinel rule does not apply to it — without a
COUNTBLANK formula there's nothing to satisfy, and `None` would only add noise.

## Repository layout

```
main.py                    CLI (--worker, --batch, --force-refresh, --requeue, --dry-run, --publish)
framework/                 Generic runner, Worker contract, validation, logging
workers/vc_research/       Schema, column map, routing, prompt.md (Investors tab)
workers/grants/            Schema, column map, routing, prompt.md (Grants / Pitches tab)
sheets/client.py           Header-resolved Google Sheets I/O (gspread)
llm/client.py              Anthropic wrapper: research() with web search, extract() without
llm/fetch.py               Plain-HTTP page fetch for the cheap research path
gmail/client.py            Gmail draft creation (draft-only; vc_research)
wix/                       Save Data Item client + allowlisted publisher
config/                    Env-driven settings + company context blurb
tests/                     Unit tests + fakes (no network calls)
```

## Setup

1. **Google Cloud** — enable the Sheets API (and Gmail API only if you want Gmail drafts),
   create a service account, download its JSON key, point
   `GOOGLE_APPLICATION_CREDENTIALS` at it.
2. **Share the sheet** with the service-account email as **Editor**.
3. **`SHEET_ID`** — the long string in the sheet URL between `/d/` and `/edit`.
4. **`ANTHROPIC_API_KEY`** — research calls use `claude-sonnet-5` by default.
5. **Gmail drafts (optional)** — needs a Workspace admin to grant domain-wide delegation
   for `gmail.compose`. Until then leave `GMAIL_ENABLED=false`; drafts still land in the
   sheet, just not in the mailbox.
6. **Wix (only for `--publish`)** — `WIX_API_KEY`, `WIX_SITE_ID`, `WIX_COLLECTION_ID`.

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

**Backfill.** All ~968 rows currently read `PULL VC DATA` because M/N/O are empty
sheet-wide. `BATCH_SIZE` defaults to 250, so the first few biweekly runs are the big,
expensive ones (~4 runs to clear it) and everything after that is small — only rows that
went stale past 90 days or were newly added. 250 also keeps a run inside the 6-hour
GitHub Actions job limit.

**Cost.** `FETCH_FIRST=true` (default) fetches a row's known website over plain HTTP and
asks the model to extract from that text — no web-search tool calls. It only falls back to
the search-enabled call when there's no usable website or the extraction came back too
thin. Set `FETCH_FIRST=false` to always use full research.

## Publishing to Wix

`--publish` is a separate step and never runs research. It pushes rows whose queue column
reads `CURRENT` — i.e. fully researched and fresh — to a Wix CMS collection via
[Save Data Item](https://dev.wix.com/docs/api-reference/business-solutions/cms/data-items/save-data-item)
(`POST /wix-data/v2/items/save`), which upserts on `dataItem.id`. The item ID is a
normalized slug of the firm name, so re-publishing updates rather than duplicating.

Only these fields ever leave the sheet: `Name`, `Website`, `Industry Focus`, `Stage`,
`City`, `State/Country`, `Application Link`, `Deadline Status`. `Email`, `Phone`,
`Contact Date`, `Status`, and every draft field are on an explicit forbidden list. It's an
allowlist, so a new sheet column is excluded by default and has to be added deliberately —
and a test fails if a disallowed header ever reaches the payload.

## Testing

```sh
pytest
```

91 tests, all against in-memory fakes — no network, no credentials. They cover the sentinel
rule, queue selection from column Q, the D:O range invariant, the Wix allowlist and
publish idempotency, channel routing, the fetch-first fallback chain, and schema
validation/retry.

## Scheduling

`.github/workflows/worker.yml` runs biweekly (09:00 UTC on the 1st and 15th) for both
workers, plus `workflow_dispatch` for manual runs with all the same flags. Required repo
secrets: `ANTHROPIC_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON_B64` (base64 of the key file),
`SHEET_ID`.

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
