# Fact quality and provenance (metadata version 1)

This change preserves the original `facts` schema, fact IDs, extracted values, units,
periods, basis strings, quotations, document IDs and PDF page numbers. It adds a
`fact_quality` table with a raw-row SHA-256 fingerprint, metadata version and JSON
metadata. This fingerprint detects raw-row changes; it is **not a PDF content hash**.
No historical evaluation files or model results are rewritten.

Metadata includes source document publication/data years where available, a
conservatively classified period, aggregation scope/location, normalized value/unit,
accounting basis, and explicit quality flags. All attribution remains **unreviewed**.
Quote/value presence and normalization do not establish semantic correctness.

## Offline commands

Use the project's installed Python environment. These commands do not construct a
model client or load an embedding model. Work on a backed-up database first.

```bash
# Read-only report; a missing database is an error, not a newly created file.
.venv/bin/python fact_quality.py data/corpus.sqlite > fact-quality-report.json

# Add/recompute only derived metadata. Original facts are unchanged.
.venv/bin/python fact_quality.py data/corpus.sqlite --migrate > fact-quality-report.json
```

For offline restoration from `eval/results/facts.csv`, first build the documents
and chunks index from the source PDFs, then create the empty legacy facts schema:

```bash
.venv/bin/python -c 'import sqlite3; from extract import FACTS_SCHEMA; db=sqlite3.connect("data/corpus.sqlite"); db.executescript(FACTS_SCHEMA); db.close()'
.venv/bin/python fact_quality.py data/corpus.sqlite --import-csv eval/results/facts.csv > fact-quality-report.json
```

Import refuses a populated facts table, unknown documents, mismatched source
companies, out-of-range pages, non-finite values and exact duplicate rows. It does
not download anything or run extraction. Document metadata must already exist.
CSV order supplies new IDs; these need not match the IDs in old evaluation logs.
Original saved logs must continue to be interpreted against their original snapshot.
The import does not prove that a quote appears in a PDF or belongs to the claimed row.

## Migration and compatibility

- Migration uses a savepoint: table creation and metadata population are atomic.
  An existing outer transaction remains owned by the caller. No SQLite
  `user_version` or existing schema columns are changed.
- Re-running refreshes derived metadata, including changed document metadata.
  Raw fingerprints and a version identify the derivation. A newer metadata version
  is refused rather than silently downgraded. Version 1 contains derived data only;
  it is not a store for human-reviewed overrides.
- Legacy readers selecting named facts columns still work. The agent derives the
  same metadata on read and needs neither write access nor a mandatory migration.
- Back up the database using SQLite's backup API or while closed; do not copy only
  the main file during active WAL writes. Metadata adds storage and an exclusive
  write phase; concurrent writers may need a retry outside this migration.
- Existing raw rows are never silently fixed or deleted. Explicit contradictions
  become flags. Unknown fiscal/calendar interpretation, missing basis, ambiguous
  emissions abbreviations (`mtCO2e`, `MT CO2e`, bare `MMT`) and unspecified gallon
  conventions stay unresolved. Conversions use a fixed explicit alias map.
- A publication year is not a fact's reporting period. Fiscal/calendar periods
  stay distinct, and cumulative/range periods are not annual observations.
- Scope derivation is deliberately narrow: explicit fleet/company-wide wording,
  or Apple's explicit location-labeled grid-mix quotations. Numerical fragments on
  Apple 2026 pages do not provide enough context to infer scope; they remain unknown.

## Fact lookup and comparisons

`query_facts` retains the company/metric/year interface. It exposes raw values plus
qualified normalized representations, basis, period type, publication year and
quality flags. Clearly location-specific Apple figures are excluded from this
company-total tool. Unknown legacy scope remains visible with an explicit warning;
this preserves access to evidence without pretending the rows are verified totals.
Range/cumulative periods are excluded when an annual year is requested but remain
visible under `year="any"`. Source quotation text is preserved independently of
metadata. No new semantic grounding policy is introduced here.

The deterministic `compatible()` helper returns reasons that observations require
qualification: differing metrics/dimensions, unresolved scope/basis, fiscal versus
calendar periods, range periods, or explicit quote-unit/year contradictions. It
never silently averages, merges, selects a restatement or chooses an accounting
basis. Compatible periods may describe different years for trend comparisons.
Compatible units alone are not evidence that two observations have the same meaning.
The tool explicitly warns about these constraints; enforcing the correctness of
arbitrary model-generated calculations remains a grounding/reliability task for PR 2.

## Extraction publication safety

A nonempty facts table is refused **before model-client construction**. Future
extractions must use a separate database/index rather than overwrite history.
Each run stores a unique `logs/extraction-runs/<id>/extraction.jsonl` and `facts.csv`;
the committed historical paths remain untouched. Limited runs publish artifacts
only. Full runs publish facts and metadata atomically only when every expected
page completed successfully. Failure cannot drop an existing facts table.

This is an intentional CLI behavior change: rerunning `extract.py` over an existing
populated corpus no longer replaces it. These safeguards were tested with fake
extractors; no live extraction was performed. `ingest.py` still rebuilds its database
destructively; safe ingestion publication remains outside this focused PR.

## Benchmark implications and limitations

Fact-tool content, qualifications, filtering and input token counts change. Annual
queries may lose cumulative observations; company queries lose confirmed location
figures. Unresolved observations remain explicitly qualified, and alternative
editions/bases are preserved. Answers, cost, latency and accuracy can therefore
change in either direction. Normalized values can also expose limitations of the
existing raw-number grounding check; that check is intentionally deferred to PR 2.

The saved **27/30 agent and 19/30 pipeline** results describe the earlier
implementation. This change produces no new benchmark result. Grounding, numeric
grading, run provenance, prompt injection defenses, agent execution budgets and
safe ingestion are unchanged. No paid call is needed to migrate or test this work.
