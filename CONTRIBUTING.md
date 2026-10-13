# Contributing to MetricPilot

Thanks for helping make local analytics easier to inspect and reproduce.

## Development

Use Python 3.11 or newer in a dedicated virtual environment. From the repository root:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m ruff check .
metricpilot eval
```

Run `metricpilot serve` to inspect the dashboard. Follow README instructions for your shell.
Offline demos and tests need no API key. Live provider changes should include an `httpx`
mock that verifies the protocol and failure path, without contacting a paid endpoint.

## Changes worth contributing

- Add a reproducible CSV fixture with a clear question, SQL, and expected result.
- Improve the bounded SQL tool without allowing data writes or access to another dataset.
- Improve result visualization while keeping query IDs and source cells inspectable.
- Document limitations with an example that others can run.

When fixing a bug, include a focused regression test for the observed behavior. Keep
frontend content in safe text nodes. Treat CSV cells, model output, and provider errors as
untrusted data. A model must never be allowed to replace the deterministic claim verifier.

Please open an issue with reproduction steps and synthetic data. Never include API keys,
real customer datasets, `.env` files, or local SQLite databases in issues or pull requests.
The project uses the MIT license; contributions are provided under the same license.
