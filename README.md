# Utilyze Automation Platform — VC Research Worker

A headless worker that researches VC investors/accelerators with an LLM (web search
enabled), validates the result against a strict JSON schema, and writes it back into a
Google Sheet — filling only blank worker-owned cells, never touching human-owned columns,
and never sending anything itself. Built as a reusable row-worker **framework**
(`framework/`) so future workers (e.g. `grants`) plug in via a new schema, column map,
prompt, and `route()` function — the runner never changes.

Current status: **framework + `vc_research` worker only.** No live run has been made
against the real spreadsheet yet — this repo has not been wired up with real credentials.
Follow the setup checklist below before running for real.

## Repository layout

```
main.py                    CLI entry point
framework/                 Generic runner, Worker contract, validation, logging
workers/vc_research/       Schema, column map, routing, prompt.md
sheets/client.py           Header-resolved Google Sheets I/O (gspread)
llm/client.py              Anthropic client wrapper (web_search tool)
gmail/client.py            Gmail draft creation (domain-wide delegation)
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
   - Duplicate the *Utilyze - Investors + Grants* spreadsheet (never point this worker
     at the production sheet during development).
   - Share the sandbox copy with the service account's email as **Editor**.
   - Set `SHEET_ID` to the sandbox spreadsheet's ID (from its URL) and `SHEET_TAB` to
     `Investors` (default).
3. **(Optional) Gmail draft creation**
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
python main.py --worker vc_research --dry-run          # preview, no writes
python main.py --worker vc_research --batch 5           # real run, 5 rows
python main.py --worker vc_research --requeue           # re-process Error/Needs Review rows
python main.py --worker vc_research --force-refresh     # overwrite non-blank business cells
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
- **`Status` / `Contact Date` / `Found` are human-owned** and structurally guarded: a
  worker's `column_map` cannot target them (raises at construction), and the runner
  asserts they're never present in a write payload.
- **Business columns** (`Website`, `Email`, `Stage`, ...) are written only to a blank cell
  unless `--force-refresh`. **Bookkeeping columns** (`Bot_Status`, `Confidence`,
  `Source_URLs`, `Research_Notes`, `Draft_*`, `Recommended_Channel`, `Last_Checked`) are
  the worker's own assessment and are refreshed every time a row is processed.
- **Retry**: an invalid/unparseable LLM response is retried up to 2× with a corrective
  nudge; after 3 total failures the row is marked `Error` with the reason in
  `Research_Notes` and no business columns are touched.
- **Confidence gate**: results below `CONFIDENCE_MIN` (default `0.5`) still get written,
  but `Bot_Status` is forced to `Needs Review` with a `[LOW CONFIDENCE: x.xx]` prefix on
  `Research_Notes`.
- **Adding a future worker** (e.g. `grants`): implement `Worker` in a new
  `workers/<name>/` package (schema, column map, `route()`, prompt) and register it in
  `main.py`'s `WORKER_REGISTRY`. Nothing in `framework/` needs to change.
