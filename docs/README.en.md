# MetricPilot

An independent, local CSV analytics agent with real SQL execution and exact query-cell provenance.
Upload a bounded UTF-8 CSV, inspect schema and profiles, ask a compatible tool-calling model to
query the dataset, and inspect KPI cards, result tables, SVG bar charts, query history, and exports.

## Quick start

Requires Python 3.11+ (CI targets 3.11 / 3.12):

```bash
python -m venv .venv
# Activate .venv for your shell.
python -m pip install -e '.[dev]'
metricpilot serve
```

Open http://127.0.0.1:8011. The default **offline demo** maps a finite set of sample questions
to real SELECT queries over seeded commerce data. Unsupported questions and imported datasets
abstain in demo mode. This is visibly labeled and does not claim general model reasoning.

```bash
metricpilot demo
metricpilot eval
python -m pytest -q
```

The eight-case synthetic evaluation checks seven fixed SQL workflows and one abstention.
It measures neither live-model accuracy nor arbitrary SQL correctness.

For live mode, copy `.env.example` to `.env`, set mode `live`, provider URL, API key and a model
supporting **Chat Completions function calling**, then restart. Responses-only models are not
supported. Paid APIs are not called by the test suite or offline commands.

## Evidence and limits

Reports cite an exact `query_id`, zero-based `row`, `column`, and `value`. The server verifies
the cell exists in this run and equals the proposed value. This proves provenance/equality,
**not that the SQL expresses the correct business question**. Inspect filters, aggregations,
units and source-data quality. SQL such as a constant UNION remains valid SQL.

CSV import: 1 MiB, 10,000 rows, 30 columns; ASCII identifier headers; immutable snapshots.
SQL runs in a fresh in-memory database containing only the selected dataset, with query-only
mode, a restrictive SQLite authorizer, function allowlist, execution and output budgets.
No Python execution, shell, filesystem tools, external databases, extensions, writes, or CTEs.
Results are capped at 200 rows / 128 KiB and expose truncation. Bar charts require nonnegative
numeric values. Floating-point arithmetic is not a fixed-point accounting engine.

CSV storage and SQL execution are local. **Live mode sends the question, schema, samples and
query results to your configured provider.** Local SQLite data is not encrypted. The app is a
single-process localhost tool for a trusted user, without authentication or multi-tenancy;
do not expose it to the internet. Interrupted runs require a new analysis.

See [architecture](architecture.md), [API contract](api-contract.md), and [security](../SECURITY.md).
MIT licensed.
