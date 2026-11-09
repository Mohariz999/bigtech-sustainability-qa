# Matched benchmark results

See the [benchmark report](../../../docs/benchmark-2026-11-09.md) for methodology,
results and per-question failure analysis.

- `verdicts.csv` records status, the common grader's verdict, gold-page overlap
  and whether the native validator flagged each answer.
- `summary.json` records per-run metrics, the changed verdicts and frozen settings.
- `corpus-manifest.json`, `embedding-manifest.json` and `environment.txt` identify
  the corpus, embedding snapshot and Python environment.

Run `python -m eval.verify_benchmark` from the repository root to check the public
verdicts and published figures offline. Full answer text, provider request IDs and
the per-request usage ledger are retained locally. Regrading answers and recomputing
latency medians require that local evidence bundle.
