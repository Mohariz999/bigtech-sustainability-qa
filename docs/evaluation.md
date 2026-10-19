# Evaluation

Everything in this project is measured against hand-written test sets that were built **before** the system they test:

| Set | What it measures | Scorer | Results |
|---|---|---|---|
| [`eval/questions.jsonl`](../eval/questions.jsonl): 30 questions | Search, pipeline answers, agent answers | `eval/run_retrieval.py`, `eval/run_answers.py` | [`retrieval.md`](../eval/results/retrieval.md), [`answers.md`](../eval/results/answers.md), [`answers_agent.md`](../eval/results/answers_agent.md) |
| [`eval/facts_gold.jsonl`](../eval/facts_gold.jsonl): 19 facts | Structured extraction | `eval/run_extraction.py` | [`extraction.md`](../eval/results/extraction.md) |

Every model call from the runs is in [`logs/`](../logs/): retrieved pages, answer, citations, tokens, cost and latency. All runs use `claude-opus-5-5`.

## The question set

Each question has the expected answer, the values the answer must contain, the cited page(s), and an exact quote from that page. `eval/check_questions.py` confirms every quote is really on its cited page, and that every "not found" answer really is absent from the reports. All 30 pass.

| Category | Count | What it tests |
|---|---|---|
| `fact` | 15 | One number on one page, spread across all five companies |
| `table` | 4 | The answer is in a data table that becomes a flat row of numbers |
| `ambiguous_year` | 2 | Two editions of the same report give different numbers |
| `restated_numbers` | 1 | Two Microsoft reports disagree about the same past year |
| `ambiguous_definition` | 1 | "100% renewable" means annual matching, not hour by hour |
| `comparison` | 3 | Needs two sources or arithmetic |
| `not_found` (incl. distractors) | 4 | The answer isn't in the reports; two have tempting near-misses |

**Page numbers are the PDF page index (1-based), not the printed page number.** They differ in most reports.

**Answer-key changes are recorded, not hidden.** After the first real run I added pages that state the same fact (each one read before adding), and corrected one wrong key (q17, Microsoft's two emissions totals). Each change has a note in the question's `notes` field. `eval/run_answers.py --rescore` re-applies the key to saved answers without new API calls.

## Search

26 answerable questions; a hit means a chunk from a correct page is in the top 5.

| Method (company + year filters) | hit@1 | hit@5 | MRR |
|---|---|---|---|
| Keyword (FTS5 / BM25) | 27% | 58% | 0.41 |
| Vector (`bge-small-en-v1.5`) | 19% | 62% | 0.35 |
| **Hybrid (Reciprocal Rank Fusion)** | 23% | **73%** | 0.42 |

| Version | hit@5 |
|---|---|
| First run | 50% |
| After fixing the cleanup step that deleted table rows, and adding verified alternative pages | 73% |
| After adding the year filter (once its bug was fixed) | 73%, with better ranking (MRR 0.38 → 0.42) |

**What still misses (7 of 26):** 3 of the 4 table questions, 2 questions without a single year that drift to the older report, and 2 where a summary sentence ranks below more detailed pages.

**Search alone can't decide "not found".** The top similarity score for the 4 not-found questions (0.70–0.79) overlaps the range for answerable ones (0.72–0.88), so no threshold separates them.

## Pipeline vs agent

| | Pipeline (`answer.py`) | **Agent (`agent.py`)** |
|---|---|---|
| Overall correct | 20/30 (67%) | **28/30 (93%)** |
| Answerable questions correct | 17/26 | **24/26** |
| Cites a correct page | 19/26 | **24/26** |
| "Not found" handled correctly | 3/4 | **4/4** |
| Cost per question | **1.7¢** | 4.6¢ |
| Latency median / p95 | **4.0s / 6.5s** | 7.7s / 12.6s |
| Model calls per question | 1 | 2.7 on average, 5 at most |
| Cost of a full 30-question run | **$0.50** | $1.38 |

| Question type | Pipeline | Agent |
|---|---|---|
| Single facts (15) | 12 | 14 |
| Tables (4) | 2 | **4** |
| Ambiguous / conflicting (4) | 2 | 3 |
| Comparisons (3) | 1 | **3** |
| Not found (4) | 3 | 4 |

### Why the pipeline missed

| Cause | Questions |
|---|---|
| Search never found the right page. In 4 of 6 cases the model said "not found" instead of guessing | q05, q06, q15, q16, q19, q26 |
| Right page found, but the answer used the older report or missed part of a definition | q20, q23, q25 |
| Gave a related figure for a "not found" question instead of saying not found | q28 |

When search found the right page, the pipeline answered correctly 16 times out of 19 (84%). **Search is the pipeline's bottleneck, not the model.**

### What the agent changed

It fixed 10 of the pipeline's misses (q05, q06, q15, q16, q19, q20, q23, q25, q26, q28):
- The **facts table** answered the table questions directly, without depending on search luck.
- The **calculator** made the comparisons reliable.

It used `query_facts` 27 times, `search_docs` 27 times and `calculator` 12 times across the 30 questions.

Its two misses are **grading problems**, and the strict score is kept:
- q04: it calculated 33.6% from the data table. Google's text rounds this to 34%, and the key requires the string "34%".
- q22: it computed +61.7%; the key accepts "62".

**Exact-string grading is too strict for free-text answers.** The next step is a number-aware check or an LLM judge, with its own spot checks.

### Grounding

The checks flagged 3 pipeline answers and 4 agent answers. None were invented facts: all were the model's own arithmetic, unit conversions (MWh → TWh), or report years taken from the source label. **No made-up numbers were found.** The check can't catch a real number attached to the wrong thing, which is another reason for an LLM judge.

## Structured extraction

223 pages (at least 20 numbers and 3 metric words) → Claude with a fixed schema of 13 metrics → Pydantic → quote and value checks → `facts` table, with a copy in [`eval/results/facts.csv`](../eval/results/facts.csv).

| | Result |
|---|---|
| Gold facts found (recall) | **19/19** |
| Facts stored | 680 |
| Facts rejected by the checks | 5 of 685 ("value not in quote") |
| Pages failed | 0 of 223 |
| **Precision, random sample of 25 checked by hand** | **19 clearly right, 5 probably right (the quote is too short to prove it), 1 wrong** |
| Cost | $3.92 (574k tokens in, 81k out), median 3.5s per page, 4 pages in parallel |

The scorer also lists every **conflict**: the same company, metric and year with different values. The 92 conflicts fall into four groups:
- **Units:** Microsoft water as "14.2 million m³" and as "14,278,800 m³".
- **Accounting basis:** Microsoft management's criteria vs standard; Google ambition-based vs GHG Protocol; Apple net vs gross.
- **Restatements between editions:** Google's 2024 data-centre electricity is 30,637,100 vs 30,825,600 MWh; Amazon's 2024 emissions are 68.25 vs 69.55 MMT.
- **Real errors:** Apple per-site electricity figures stored under the company-level metric.
