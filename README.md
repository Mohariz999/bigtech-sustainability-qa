# bigtech-sustainability-qa

Question answering and structured extraction over Big Tech sustainability reports (Google, Microsoft, Meta, Amazon, Apple, two years each where available). Every answer cites a report and page, the system says "not found" instead of guessing, and a 30-question evaluation set measures how often it is right.

**Status:** work in progress. Documents, eval set, ingest and retrieval are done. **Hybrid search finds the right page in the top 5 for 73% of answerable questions** (keyword 58%, vector 62%); see [eval/results/retrieval.md](eval/results/retrieval.md). **Full pipeline with Claude: 67% correct on all 30 questions, 84% when search finds the right page, 1.7 cents and 4 seconds per question**; see [eval/results/answers.md](eval/results/answers.md). See [NOTES.md](NOTES.md) for the plan and the failure log.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Get the documents

```bash
python fetch_docs.py
```

This downloads 10 public reports into `docs/` and writes `docs/manifest.csv`. The PDFs are not committed to this repo.

## Check the eval set

```bash
.venv/bin/python eval/check_questions.py
```

This confirms each question's evidence quote is on its cited page and that each "not found" answer really is absent.

## Build the index and search

```bash
.venv/bin/python ingest.py
```

```bash
.venv/bin/python search.py "What was Google's PUE in 2025?"
```

```bash
.venv/bin/python eval/run_retrieval.py
```

`ingest.py` writes `data/corpus.sqlite` (about 2.5 minutes). `search.py` runs one hybrid search. `run_retrieval.py` scores every search method on the eval set.

## Answer questions with Claude

Copy `.env.example` to `.env` and paste your Anthropic API key into it (`.env` is gitignored).

```bash
.venv/bin/python answer.py "What was Google's PUE in 2025?"
```

```bash
.venv/bin/python eval/run_answers.py --dry-run
```

```bash
.venv/bin/python eval/run_answers.py --limit 3
```

```bash
.venv/bin/python eval/run_answers.py
```

`--dry-run` estimates the cost without calling the API. A real run of all 30 questions cost $0.50. `--rescore` re-applies an edited answer key to the saved answers for free. The full run writes `eval/results/answers.md` and logs every call (tokens, cost, latency) to `logs/answers.jsonl`.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The answer step is tested with a fake Claude client, so the tests are free and need no key.
