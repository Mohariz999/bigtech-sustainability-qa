# bigtech-sustainability-qa

Question answering and structured extraction over Big Tech sustainability reports (Google, Microsoft, Meta, Amazon, Apple, two years each where available). Every answer cites a report and page, the system says "not found" instead of guessing, and a 30-question evaluation set measures how often it is right.

**Results** (30-question eval, `claude-opus-5-5`):

| | Result |
|---|---|
| Search: right page in top 5 | 73% (keyword 58%, vector 62%) |
| Pipeline (search → Claude) | 67% correct, 1.7¢ and 4s per question |
| Structured extraction | 680 facts from 223 pages, 19/19 known facts found, $3.92 |
| **Tool-calling agent** (search + facts table + calculator) | **93% correct**, 4.6¢ and 8s per question |

Details: [eval/results/](eval/results/). Plan, failure log and what I'd do next: [NOTES.md](NOTES.md).

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

## Extract facts and run the agent

```bash
.venv/bin/python extract.py --dry-run
```

```bash
.venv/bin/python extract.py
```

```bash
.venv/bin/python eval/run_extraction.py
```

```bash
.venv/bin/python agent.py "How much did Google's data centre electricity use grow from 2024 to 2025?"
```

```bash
.venv/bin/python eval/run_answers.py --system agent
```

`extract.py` writes a `facts` table (about $4 for all 223 pages; `--dry-run` shows the estimate first) and `eval/results/facts.csv`. `run_extraction.py` scores it against 19 hand-checked facts. The agent needs the facts table.

## Tests

```bash
.venv/bin/python -m pytest -q
```

The answer step, extraction and agent are tested with a fake Claude client (31 tests), so the tests are free and need no key.
