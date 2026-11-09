# finance-digest — agent context

Email digest for a euro saver in Romania: BNR EUR/RON and USD/RON, EUR-quoted
UCITS ETFs (SXR8, SXRV, VWCE, XEON), ECB rates, and Fidelis/Tezaur and EUR
deposit offers. Runs on: GitHub Actions (public repo), dispatched Tue–Sat daily
and Sunday weekly.

## Commands
- `make test` — pytest on fixtures only, no network (safe). `make lint` / `make fmt` — ruff.
- `make preview` — renders `tests/fixtures/preview_*.json` into `preview.html` (safe, offline).
- `make collect` — live fetch with local state in `.local/` (a local egress hook may block it).
- `make send-test` — sends a real test email using `MAIL_*` from `.env`.
- `make run [FORCE=true] [MODE=weekly]` — dispatches `digest.yml` on GitHub:
  real email plus commits to branch `data`. `make probe` — dispatches `probe.yml`.
- CLI entry point `finance` (`src/finance/cli.py`): `collect`, `prompt`,
  `merge-offers`, `should-send`, `render`, `send`.

## Layout
- `src/finance/series.py` — tracked ids, tickers, thresholds; `state/series.json`.
- `src/finance/sources.py` — BNR / ECB / justETF / Yahoo parsers and `collect()`.
- `src/finance/movers.py` — per-horizon changes, EUR/USD split, XEON yield, "notable".
- `src/finance/offers.py`, `prose.py` — validate Claude's offers and explanations.
- `src/finance/outbox.py` — pending -> sent, up to 5 attempts (`MAX_ATTEMPTS`).
- `src/finance/email/` — Jinja2 templates + CSS inlined for Gmail.
- `prompt/discover.md` (offers), `prompt/explain.md` (causes of moves).
- `scripts/commit-data.sh` — commit/push `out/` to branch `data` only if changed.

## How it works
- No `schedule` trigger; gh-workflow-scheduler dispatches `digest.yml` Tue–Sat
  05:30 UTC (`mode=daily`, previous trading day) and Sunday 07:00 UTC (`mode=weekly`).
- Branch `data` is a worktree at `out/` (created orphan on first run) holding
  `state/`, `digests/`, `outbox/`; weekly stem is `<date>-saptamanal`.
- Order: retry pending emails -> `finance collect` -> Claude `discover` (JSON offers)
  -> `merge-offers` -> `should-send` -> Claude `explain` -> `render` -> commit ->
  Gmail SMTP send -> commit delivery status.
- Every number comes from Python. Claude only prints JSON (tools limited to
  WebSearch/WebFetch, Bash/Edit/Write disallowed); explanations without a link are dropped.
- Both Claude steps are `continue-on-error` with 7-min timeouts; job timeout 20 min.
- Secrets: `CLAUDE_CODE_OAUTH_TOKEN` (subscription), `MAIL_USERNAME`, `MAIL_PASSWORD`.

## Gotchas
- A green run can hide a failed Claude step: read `gh run view <id> --log` and grep
  `##[error]`, not `--log-failed`.
- The stripped OAuth token no longer matches GitHub's secret mask; the workflow
  re-masks it with `::add-mask::` before use. Keep that when editing the steps.
- State and the pending outbox entry are committed before sending, so a failed
  send is retried next run; do not reorder those steps.
- Yahoo returns 429 to runner IPs (fallback only); Stooq sits behind a JS bot wall.
  Three failures in a row show a "surse degradate" banner.
- A fresh ECB point alone is not news; only a policy rate prints its last change,
  otherwise Monday/holiday digests go out with nothing new (a2ec853).
- A data point for a session not yet closed is dropped.

## Related repos
- `gh-workflow-scheduler` — two entries in `src/schedules.ts` (`finance-digest`,
  `finance-digest-weekly`).
- `flow-doctor` — `finance` check (run result, hidden step failures, outbox,
  `report.failed`) and the OAuth token's 1-year expiry.

## Do not
- No investment advice in prompts or templates: the digest reports, never recommends.
- Do not let Claude write state or files; it only prints JSON.
- Do not edit branch `data` by hand or add a `schedule:` trigger.
