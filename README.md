# Utilyze Automation Platform

Headless workers that research VC investors and grant/pitch opportunities with an LLM
(web search enabled), validate the result against a strict JSON schema, and write it back
into a Google Sheet — filling only blank worker-owned cells, never touching human-owned
columns, and never sending or applying anything themselves. Built as a reusable row-worker
**framework** (`framework/`): each worker plugs in via its own schema, column map,
`route()` function, and prompt — the runner never changes.

Two workers ship today:

- **`vc_research`** — researches VC investors/accelerators, recommends an outreach
  channel, and drafts an intro email (optionally creating a Gmail draft).
- **`grants`** — research-only: eligibility, fit score, requirements, and a draft
  application outline for a funding/pitch/accelerator opportunity. No send step.

Current status: **no live run has been made against the real spreadsheet(s) yet** — this
repo has not been wired up with real credentials. Follow the setup checklist below before
running for real.

## Repository layout

```
main.py                    CLI entry point (--worker vc_research|grants)
framework/                 Generic runner, Worker contract, validation, logging
workers/vc_research/       Schema, column map, routing, prompt.md (Investors tab)
workers/grants/            Schema, column map, routing, prompt.md (Grants / Pitches tab)
sheets/client.py           Header-resolved Google Sheets I/O (gspread)
llm/client.py              Anthropic client wrapper (web_search tool)
gmail/client.py            Gmail draft creation (domain-wide delegation, vc_research only)
config/                    Env-driven settings + company context blurb
tests/                     Unit tests + fakes (no network calls)
.github/workflows/         Scheduled cron run
```

## One-time setup (blocks any live run)

1. **Google Cloud project**
   - Enable the **Google Sheets API** (and **Gmail API** if you plan to enable Gmail
     drafts) in a GCP project.
   - Create a **service account**, download its JSON key, and set
     `GOOGLE_APPLICATION_CREDENTIALS` to that file's path.
2. **Sandbox spreadsheet**
   - Duplicate the *Utilyze - Investors + Grants* spreadsheet (never point either worker
     at the production sheet during development).
   - Share the sandbox copy with the service account's email as **Editor**.
   - Set `SHEET_ID` to the sandbox spreadsheet's ID (from its URL). Each worker reads its
     own tab (`vc_research` → `Investors`, `grants` → `Grants / Pitches`) — this is fixed
     per worker, not an env var, since a single `SHEET_TAB` env var can't serve both.
3. **(Optional) Gmail draft creation — `vc_research` only**
   - Requires a Google Workspace admin to grant **domain-wide delegation** on the
     service account for the `https://www.googleapis.com/auth/gmail.compose` scope,
     scoped to the sender mailbox (`GMAIL_SENDER`, default `ana.valentino@utilyze.ai`).
   - Until that's granted, leave `GMAIL_ENABLED=false` (the default) — the worker still
     writes `Draft_Subject`/`Draft_Body` to the sheet either way, just without creating a
     live Gmail draft.
4. **Anthropic API key**
   - Set `ANTHROPIC_API_KEY`. Research calls use `claude-sonnet-5` by default (see
     `ANTHROPIC_MODEL` in `.env.example` — override to `claude-opus-5` for higher-quality,
     higher-cost research).

Copy `.env.example` to `.env` and fill in the values above.

## Running

```sh
pip install -r requirements-dev.txt

python main.py --worker vc_research --dry-run           # preview, no writes
python main.py --worker vc_research --batch 5            # real run, 5 rows
python main.py --worker grants --dry-run                 # same flags, grants tab
python main.py --worker grants --requeue                 # re-process Error/Needs Review rows
python main.py --worker vc_research --force-refresh      # overwrite non-blank business cells
```

`--dry-run` still calls the LLM (so you can inspect real proposed research/draft output in
the logs) but never writes to the sheet.

## Testing

```sh
pip install -r requirements-dev.txt
pytest
```

All tests run against in-memory fakes (`tests/fixtures/`) — no network calls, no real
credentials required.

## Design notes

- **Header resolution is always by name**, never by fixed column index (`sheets/client.py`)
  — the real sheet has trailing blank columns and headers that may shift.
- **Human-owned columns are per-worker** (`Worker.human_owned_columns` — e.g. VC's
  `Status`/`Contact Date`/`Found`; grants' `Status`/`Name`/`Website or Application Link`)
  and structurally guarded: a worker's `column_map` cannot target them (raises at
  construction), and the runner asserts they're never present in a write payload.
- **Business columns** (VC's `Website`/`Email`/`Stage`/...; grants' `Category`/`Deadline`/
  `Email`) are written only to a blank cell unless `--force-refresh`. **Bookkeeping
  columns** — `Bot_Status`, `Confidence`, `Source_URLs`, `Research_Notes`, `Last_Checked`
  (shared by every worker, `framework.worker_base.CORE_COLUMNS`), plus each worker's own
  route column (`Recommended_Channel` / `Recommended_Action`) and extras (VC's `Draft_*`;
  grants' `Fit_Score`/`Eligibility`/`Requirements`/`Draft_Outline`) — are the worker's own
  assessment and are refreshed every time a row is processed.
- **Retry**: an invalid/unparseable LLM response is retried up to 2× with a corrective
  nudge; after 3 total failures the row is marked `Error` with the reason in
  `Research_Notes` and no business columns are touched.
- **Confidence gate**: results below `CONFIDENCE_MIN` (default `0.5`) still get written,
  but `Bot_Status` is forced to `Needs Review` with a `[LOW CONFIDENCE: x.xx]` prefix on
  `Research_Notes`.
- **Composite columns**: a worker whose sheet needs joined/derived columns (grants'
  `Eligibility` = eligible + reason, `Requirements` = joined list) overrides
  `Worker.compute_business_updates()` instead of relying on the default 1:1 `column_map`
  walk that `vc_research` uses. `route()` output (`Recommended_Channel` /
  `Recommended_Action`) is always deterministic — computed by the worker from the
  validated result, not passed through verbatim from the LLM.
- **Adding a future worker**: implement `Worker` in a new `workers/<name>/` package
  (schema, column map or `compute_business_updates()` override, `route()`, prompt) and
  register it in `main.py`'s `WORKER_REGISTRY`. Nothing in `framework/` needs to change —
  `grants` was added this way on top of the same runner `vc_research` uses.
