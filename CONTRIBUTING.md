# Development and reproducibility

Use Python 3.12 or 3.13 and a fresh virtual environment. Commands below assume a
POSIX shell and are run from the repository root.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip check
.venv/bin/python -m ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python -m eval.verify_benchmark
```

Use `python3.13` when appropriate. Tests need no API key, PDF, corpus database or
embedding model. An autouse fixture blocks socket connections during tests and
removes API-key environment variables; fake clients must be used. Package installation
needs internet access, but tests themselves do not. The fixture does not cover
arbitrary subprocess networking or network activity at module-import time: do not
instantiate models or clients with side effects during collection.

## Scope of quality checks

- Ruff is a pinned development dependency, configured for syntax/undefined-name and
  related correctness checks (`E9`, `F63`, `F7`, `F82`). It is not a full security or
  type checker. Avoid unrelated formatting rewrites while fixing behavior.
- `pip check` verifies installed dependency compatibility, not vulnerability absence.
- CI targets Python 3.12/3.13 with a timeout, minimal read permission and no persisted
  checkout credentials. Action commits are pinned; Dependabot can propose updates
  monthly. Updates are reviewed, not auto-merged.
- Strict static typing and whole-repository formatting are deferred: the fake-client
  interfaces and dictionary records need a focused typing change, not blanket ignores.
  No claim of full type safety or formatting enforcement is made.
- Direct dependencies are pinned; there is no cross-platform transitive lockfile.
  To reproduce a particular environment, record Python/OS/architecture and save
  `python -m pip freeze` with that run's artifacts. Do not present a macOS snapshot
  as a universal Linux lockfile. Review model/SDK updates separately from documentation.

## Source data and setup

The README describes fresh PDF/index creation and free restoration of the committed
facts CSV. Fetching PDFs and the first local embedding model requires internet, but
uses no paid embedding service. The test suite skips both. Paid Claude stages are
separate and require an explicit budget decision.

Back up valuable databases before ingestion or migration. Ingestion currently
deletes/rebuilds the database. A consistent SQLite backup is necessary if WAL or
concurrent writes are involved. Fact import refuses nonempty stores; metadata
migration preserves raw facts but does not review their meaning. Restored CSV fact
IDs can differ from old logs.

Do not run `extract.py` to restore saved facts: it is paid and requires an empty fact
store. Use the offline import in README. Do not commit `.env`, PDFs, local databases
or generated run directories. Historical public artifacts under `logs/*.jsonl` and
`eval/results/` are tracked deliberately and must not be silently rewritten.

## Evaluation discipline

Keep fixed questions/gold data unchanged unless independently correcting an error,
with evidence and a documented version change. No grading criterion may be tuned
solely to increase a published score. Add offline regressions for behavior changes.

New runs contain a manifest and per-question records under
`logs/evaluation-runs/<id>/`. A rescore creates derived records and never generates
new answers. Its code revision identifies the grader, not necessarily the generator
of historical responses. Legacy outputs lack some metadata and end-to-end timings;
report those gaps instead of filling them with estimates.

The final paid comparison must follow
[the approval-gated protocol](docs/evidence-and-evaluation.md#final-live-comparison--approval-required).
Record failed as well as successful questions and report equal/lower accuracy
transparently. See the [matched benchmark report](docs/benchmark-2026-11-09.md) for an example.
The benchmark verifier checks saved evidence offline and does not rewrite it.
