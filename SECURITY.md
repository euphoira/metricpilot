# Security boundaries

MetricPilot is a single-process localhost tool for a trusted user. It has no authentication,
tenant isolation, or internet-facing deployment configuration. The CLI binds to `127.0.0.1`.
Host and write-origin checks, body limits, a local CSP, and no CORS reduce browser-origin abuse;
they do not protect against a malicious process already running as the local user.

CSV snapshots, questions, queries, result cells and reports are persisted unencrypted in the
configured data directory. In live mode, questions, schema, sample values and query results are
transmitted to the configured model provider. Do not import data you cannot send to that provider.
Secrets belong in the ignored `.env` file or environment variables; never in committed examples.

Agent SQL uses a fresh in-memory SQLite connection with only the selected dataset. It has
query-only mode, an authorizer, allowlisted functions, execution limits, and bounded result reads.
No extension loading, shell, Python execution, external file/database access or mutations are exposed.
The selected dataset table remains readable by design, so these controls are not row-level access
policies or sensitive-cell redaction. Prompt-injection instructions remain untrusted data.

`verified` checks result-cell provenance and equality only. It does not prove a correct business
query, trustworthy source data, appropriate units, causal inference, or accounting precision.

If reporting a vulnerability, include a minimal synthetic reproduction and impact without posting
credentials or private datasets. Until private reporting is configured on GitHub, request a private
contact channel in an issue without publishing exploit details involving a real system.
