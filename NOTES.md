# Project notes

## The project in one line
A question-answering tool over Big Tech sustainability reports. It answers with a citation (report + page) and says "not found" instead of guessing, and an evaluation set measures how often it is right.

## Step 1: documents (done)

10 public PDFs, 842 pages, about 400,000 words. Listed in `docs/manifest.csv`; `python fetch_docs.py` downloads them again.

**Why these:** they're easy to read and all about the same things: data centres, electricity, water and carbon. Most companies have **two years** of reports. That's deliberate: the same question can have a different answer depending on the year, which is one of the main things the eval tests.

| Doc | Pages | What it's about |
|---|---|---|
| **Google Environmental Report 2026** (2025 data) | 117 | Google's electricity demand grew 37% in a year because of AI. It still matched 100% of electricity with renewable purchases over the year, but only about 65% hour by hour ("24/7 carbon-free energy"). Data-centre PUE is 1.09. It signed over 12 GW of new clean energy deals. Total emissions are up 18%, and water use is up 34% (78% replenished). It has a large data appendix (p93) with electricity by year. |
| **Google Environmental Report 2025** (2024 data) | 120 | The previous edition. CFE 66%, water replenished 64%, PUE also 1.09. Useful for "what changed?" questions, and as a trap for out-of-date answers. |
| **Microsoft Environmental Sustainability Report 2026** (FY25) | 66 | "Responsibly building the AI future." 100% renewable electricity matching in FY25. Up to 40 GW of renewable deals (19 GW online). Water replenished (14.2 million m³) exceeded withdrawals for the first time. Datacentre water efficiency (WUE) improved 25%. Few detailed numbers: those are in the fact sheet. |
| **Microsoft Environmental Data Fact Sheet 2026** | 28 | Tables only: emissions by scope for FY20–FY25, electricity, water, waste. Total FY25 emissions were 21.1M tCO2e. Market-based Scope 2 jumped about 10x in one year. |
| **Microsoft Environmental Sustainability Report 2025** (FY24) | 90 | The previous edition. It says total emissions were +23.4% vs 2020. The 2026 fact sheet's numbers don't match that figure, which suggests the history was restated. |
| **Meta Sustainability Report 2025** (2024 data) | 74 | Net-zero operations since 2020, 100% clean energy matching, 27 owned data-centre locations, and a water-positive goal for 2030. It's short on numbers: PUE and electricity use are in a separate "Environmental Data Index" that isn't in this set. |
| **Amazon Sustainability Report 2025** (2025 data) | 51 | Carbon footprint 80.85 MMT CO2e, up 16%. 100% renewable electricity matching for the 3rd year. Data-centre PUE is 1.14 (cloud industry average 1.25). 75% of the way to water positive. |
| **Amazon Sustainability Report 2024** (2024 data) | 57 | The previous edition. PUE 1.15. It covers lower-carbon steel and concrete used in new AWS data centres. |
| **Apple Environmental Progress Report 2026** (FY25) | 113 | Emissions are over 60% below 2015. 30% of product materials are recycled or renewable. All 8 Apple-owned data centres are water-certified. 100% renewable electricity for offices, stores and data centres since 2018. It covers products more than data centres. |
| **Apple Environmental Progress Report 2025** (FY24) | 126 | The previous edition, with the same structure. |

## Step 2: eval questions (done)

30 questions in `eval/questions.jsonl`. `python eval/check_questions.py` checks every one against the PDFs: 30/30 pass.

| Category | Count | What it tests |
|---|---|---|
| `fact` | 15 | Basic lookup: one number on one page, spread across all 5 companies |
| `table` | 4 | The answer is in a data table that becomes a flat row of numbers once extracted |
| `ambiguous_year` | 2 | Two editions of the same report give different numbers |
| `restated_numbers` | 1 | Microsoft's two reports disagree about the same past year |
| `ambiguous_definition` | 1 | "100% renewable" means annual matching, not hour by hour (65%) |
| `comparison` | 3 | Needs two documents or some arithmetic, which is where an agent with tools should help |
| `not_found` (+ distractors) | 4 | The answer isn't there. Two have tempting near-misses |

## What has gone wrong so far (the "where it broke" log)

1. **A sentence split across a page break.** Google's "we again matched 100%... renewable energy" sentence starts on p4 and ends on p5, with page-header text ("Introduction Impact stories Detailed disclosures...") in the middle. A chunker that splits by page cuts the sentence in half.
2. **Tables turn into rows of numbers.** Google's electricity table becomes "Data centers MWh 17,429,800 20,616,500 23,980,800 30,637,100 42,415,800". The years are in a header on a different line, so the system has to work out that the last number is 2025.
3. **Key numbers are in a different document.** Microsoft's main report has almost no totals; they're in the separate fact sheet. Meta's PUE isn't in its report at all.
4. **Restated history.** Microsoft's 2025 report says emissions were +23.4% vs 2020, but the 2026 fact sheet's own figures give about +28% for the same year.
5. **My own checker was wrong twice.** Case-insensitive "PUE" matched "PUErto Rico" in an Apple report. The fix is to match whole words only. (The first, energy-themed version of this project had the same bug: "NESO" matched "MinNESOta".)
6. **Distractors.** "How many data centres does Amazon operate?" isn't answered anywhere, but the report says "AWS constructed 36 data centers with lower-carbon steel", which looks like an answer.
7. **My header/footer cleanup deleted table data.** To remove repeated headers, I deleted lines that appear on many pages, after replacing digits with `#` so "Page 12" and "Page 13" match. But table rows like "456,119 429,405 288,029" also normalise to the same pattern, so whole rows of Microsoft's emissions table disappeared. Found by an automatic check ("is each question's evidence still in the chunks?"). Fix: only remove short lines with at most one number, near the top or bottom of a page.
8. **My answer key was too strict.** The same fact is often on several pages (highlights page, detail page, appendix). Six "misses" had actually found the fact on another page. I read each one before adding it to the key. One I deliberately **didn't** add: Google 2025 report p84 also says "PUE 1.09", but that's the 2024 figure. Same number, wrong source.
9. **The year filter first made things worse.** "Since 2021", "by 2030" and "2020 baseline" are years too. Filtering on them matched no documents, and a bug made vector search return junk instead of nothing. Fix: only filter on years some report actually has data for, and never return chunks outside the filter.
10. **"Not found" can't be detected by search score alone.** The best-match similarity for the 4 not-found questions (0.70–0.79) falls inside the range for answerable ones (0.72–0.88, median 0.80). No threshold separates them, so the "not found" decision has to come from the LLM reading the passages (step 5).

## Step 3 + 4: ingest and retrieval (done)

- `ingest.py`: PDF → clean pages → 200-word chunks with 50-word overlap (never crossing a page, so every chunk has one page to cite) → SQLite (`documents`, `chunks`, FTS5 keyword index, one embedding per chunk). 2,787 chunks, about 2.5 minutes on a laptop.
- `search.py`: keyword (SQLite FTS5 / BM25), vector (`bge-small-en-v1.5`, runs locally, free), and hybrid (Reciprocal Rank Fusion). Optional company and data-year filters run in SQL before ranking, parsed from the question text.
- `eval/run_retrieval.py`: scores every method on the 26 answerable questions. Results: `eval/results/retrieval.md`.

**Results (hit@5 = right page in the top 5):**

| Version | hit@5 |
|---|---|
| First run (hybrid + company filter) | 50% |
| + fixed table-deleting bug and corrected answer key | 73% |
| + year filter, after fixing its bug (best: hybrid + company + year) | **73%** (hit@1 19% → 23%, MRR 0.38 → 0.42) |

| Method (company + year filter) | hit@5 |
|---|---|
| keyword only | 58% |
| vector only | 62% |
| **hybrid** | **73%** |

Hybrid beats both methods on their own, which supports combining them.

**What still fails (7 of 26):** 3 of 4 **table** questions (numbers in a flat row; the question words aren't near them), 2 questions with **no single year** that drift to the older report, and 2 where the answer is in a summary sentence that ranks below more detailed pages. Fix for the tables: step 6 (extract table values into a `facts` table and query it with SQL).

## Step 5 results: first real run (all 30 questions, `claude-opus-5-5`)

Full output: `eval/results/answers.md`. Every call is in `logs/answers.jsonl`.

| Metric | Result |
|---|---|
| Overall correct | **20/30 (67%)**. The raw score was 19/30; one answer-key error was fixed (see below) |
| Answer correct (answerable) | 17/26 (65%) |
| Says "not found" when it should | 3/4 |
| **Correct when search found the right page** | **16/19 (84%)** |
| Made-up numbers caught | 0. The 3 grounding flags were the model's own arithmetic or report year |
| Cost | **$0.50 for 30 questions, 1.7 cents per question** (82k tokens in, 9k out) |
| Latency | median 4.0s, p95 6.5s, max 7.3s |
| Retries / errors | 0 / 0 |

**Why the 10 failures happened:**

| Cause | Questions | What it means |
|---|---|---|
| Search never found the right page | q05, q06, q15, q16, q19, q26 (6) | The main bottleneck. In 4 of 6 the model **said "not found" instead of guessing**. q06 answered from the older report instead. |
| Right page found, answer incomplete | q20 (used the 2024 figure), q23 (missed the 65% hourly figure), q25 (said it couldn't compare) | The model was cautious. Two of these are the year/definition traps working as designed. |
| Fell for a "not found" trap | q28 (IEA projection) | It gave a related Google projection and noted it wasn't attributed to the IEA, but still marked it "answered". It should have said not found. |

**More things that went wrong (the log continues):**

11. **My answer key was wrong, and the model was right.** For Microsoft's FY25 total emissions, the model said 20,290,000 tCO2e and I'd marked it wrong (expecting 21,121,000). Reading the fact sheet again: Microsoft publishes **two totals**, Table 1A under standard GHG Protocol accounting (21.1M) and Table 1B "with management's criteria" (20.3M), which is the one the main report uses. I fixed the key, kept a note, and report both scores. `--rescore` re-applies the key to the saved answers for free.
12. **My grounding check had a false alarm.** In the 3-question trial, the model wrote "(2026 report)", taking the year from the source label I give it, and my check flagged "2026" as not in the passage. Fixed: the label counts too, and there's a test for it.
13. **Derived numbers get flagged, and that's correct.** The model added Amazon's Scope 1+2+3 to get 80.85 and subtracted two PUEs to get 0.05. Both are right, but they're not in the source text, so the check flags them. LLM arithmetic should be verified, which is why the agent step adds a calculator tool.
14. **A bad API key crashed the whole run.** The first try used a truncated key, and the runner died with a traceback. Now it stops with a clear message, and other API errors are recorded per question instead of crashing the run.

**What I'd fix next, in order:** (1) retrieval for tables, via the facts table in step 6; (2) default to the most recent report when no year is given (q06, q20); (3) tighten the prompt so "related but not the same" means not_found (q28).

## Step 7 + 8 results: tool-calling agent vs pipeline (same 30 questions, same scoring)

`agent.py`: Claude with three tools, `search_docs`, `query_facts` (the extracted facts table, structured inputs, not raw SQL) and `calculator` (arithmetic only, no code execution), in a hand-written loop capped at 8 model calls. Full results: `eval/results/answers_agent.md`, every call in `logs/answers_agent.jsonl`.

| | Pipeline (`answer.py`) | **Agent (`agent.py`)** |
|---|---|---|
| Overall correct | 20/30 (67%) | **28/30 (93%)** |
| Answerable questions correct | 17/26 | **24/26** |
| Cites a correct page | 19/26 | **24/26** |
| Says "not found" correctly | 3/4 | **4/4** |
| Cost per question | **1.7¢** | 4.6¢ (2.7×) |
| Latency median / p95 | **4.0s / 6.5s** | 7.7s / 12.6s |
| Model calls per question | 1 | 2.7 on average, 5 at most |
| Whole eval run | **$0.50** | $1.38 |

| Question type | Pipeline | Agent |
|---|---|---|
| Single facts (15) | 12 | 14 |
| Tables (4) | 2 | **4** |
| Ambiguous / conflicting (4) | 2 | 3 |
| Comparisons (3) | 1 | **3** |
| Not found (4) | 3 | 4 |

**What changed:** the agent fixed 10 pipeline failures (q05, q06, q15, q16, q19, q20, q23, q25, q26, q28). The facts table solved the table questions, and the calculator solved the comparisons. It used `query_facts` 27 times, `search_docs` 27 times and `calculator` 12 times. Example (q26): it looked up both years, calculated 38.4% growth, and **noticed by itself** that the 2025 report gives a different 2024 figure, so it reported both growth rates.

**The 2 "misses" are grading problems, not agent errors:**
- q04: the agent calculated **33.6%** from the data table, which Google's text rounds to "34%". The scorer wants the exact string "34%".
- q22: the agent computed **+61.7%**, and the key accepts "62".
I kept the strict score (28/30) instead of editing the key to fit. But it shows that **exact-string grading is too strict for free-text answers**. Next would be a number-aware check (33.6 ≈ 34) or an LLM judge, with its own spot checks.

**Things that went wrong:**

19. **A misleading metric.** The report counted "retries" as "questions with more than one model call", which is every question for an agent, so it showed "30 retries". It now shows model calls per question for the agent.
20. **The rounding check was too strict.** The model wrote "38.4%" when the calculator said 38.4446, and the grounding check flagged it. Now a rounded version of a source number counts as grounded (shared helper in `answer.py`, with a test).

**When to use which:** the agent wins on accuracy (+26 points), the pipeline on cost and speed. In production I'd send simple single-fact questions down the pipeline and comparisons/tables to the agent, or just use the agent if 4.6¢ and about 8s are acceptable for the use case. The eval makes that a measured decision instead of a guess.

## Step 6 results: structured extraction (`extract.py`)

223 candidate pages (20+ numbers and 3+ metric words) → Claude with a fixed schema of 13 metrics → Pydantic → quote and value checks → `facts` table (copy in `eval/results/facts.csv`). Scored by `eval/run_extraction.py`, results in `eval/results/extraction.md`.

| Metric | Result |
|---|---|
| Known facts found correctly (recall) | **19/19** (Google, Microsoft, Amazon; PUE, electricity, emissions, water, CFE, renewables) |
| Facts stored | 680 |
| Facts rejected by the checks | 5 of 685, all "value not in quote" |
| Pages failed | 0 of 223 |
| **Precision, random sample of 25 checked by hand** | **19 clearly right, 5 probably right but the quote is too short to prove it, 1 wrong** |
| Cost / time | **$3.92**, 574k tokens in / 81k out, median 3.5s per page, 4 pages in parallel |

**What the 92 "conflicts" (same company, metric and year, different values) turned out to be:**
- **Same number in different units:** Microsoft water as "14.2 million m³" and as "14,278,800 m³". Needs unit normalisation.
- **Different accounting bases, all real:** Microsoft management's criteria vs standard, Google ambition-based vs GHG-Protocol, Apple net vs gross.
- **Restatements between report years:** Google's 2024 data-centre electricity is 30,637,100 MWh in the 2026 report but 30,825,600 in the 2025 report; Amazon's 2024 total emissions are 68.25 vs 69.55.
- **Real errors:** Apple "data-centre electricity" collected per-site figures (e.g. one data centre's 563M kWh) under the company-level metric.

**Things that went wrong in extraction:**

15. **Quotes shortened with "...".** In the 5-page trial, 6 of 38 facts were rejected because the model abbreviated table rows. One prompt line ("never shorten with '...'") took that to 5 of 685 on the full run.
16. **A check can pass without meaning much.** Some quotes are just fragments ("47.40 2.76"): the value is there, so the check passes, but the quote doesn't show which row or year it came from. Fix: require the row label in the quote for table rows.
17. **Site-level vs company-level.** The schema has no field for "this is one data centre, not the company total", so Apple's per-site figures landed in the company metric. Fix: add a `scope` field (company / site / region).
18. **Scientific notation bug (agent).** `{value:g}` turned 42,415,800 into "4.24158e+07" when showing facts to the agent. Caught by a unit test before any real run.

**What I'd do next:** normalise units (MWh ↔ TWh, million m³ ↔ m³), add the `scope` field, require row labels in table quotes, and give a human a review screen for the conflicts.

## Step 5: answering with citations (how it works)

`answer.py`: hybrid search → top 5 passages → Claude (`claude-opus-5-5`, effort `medium`) → JSON answer → checks.

- **Structured output:** the API is told to reply in a fixed JSON schema (`status`, `answer`, `citations`). The reply is then validated with Pydantic. If it's invalid, I retry once with the error message; if it's still invalid, the result is an error, never a guess.
- **The model cites passage numbers, not documents.** My code maps [3] to (doc_id, page), so the model can't invent a source.
- **Grounding check after the answer:** every quote must appear in its cited passage, and every number in the answer must appear in the cited passages. Otherwise the answer is flagged.
- **No passages → no LLM call** (no cost).
- **Reliability:** 90-second timeout, 3 automatic retries with backoff (SDK), refusal handled, and a server-side fallback model if Claude declines.
- **Cost + latency logged per call:** tokens in/out, $ (cache reads and writes priced separately), seconds. `eval/run_answers.py --dry-run` estimates a full run at **about $0.61, roughly 2 cents per question**, before spending anything.
- **16 unit tests** (`tests/test_answer.py`) use a fake Claude client, so the logic is tested for free: made-up numbers get flagged, bad quotes get flagged, invalid JSON is retried, refusals are handled, and the scoring rules work.
- **Prompt caching: deliberately not used.** The fixed system prompt is below the minimum size the API caches, so turning it on would do nothing. If the prompt grew, it would be worth it.

## Plan

| # | Step | What gets built | Capability |
|---|---|---|---|
| 1 | Documents ✅ | 10 reports + manifest | – |
| 2 | Eval questions ✅ | 30 checked questions | Evaluation |
| 3 | Ingest + chunk into **SQLite** ✅ | `documents` and `chunks` tables (company, data year, page) | Structured, queryable data; SQL |
| 4 | Retrieval ✅ | Hybrid search, company/year filter first: **73% hit@5** | Retrieval |
| 5 | Answer with citations ✅ **67% correct, 84% when search finds the page** | Answers only from retrieved text, cites report + page, otherwise "not found" | Retrieval, failure modes |
| 6 | Structured extraction ✅ **19/19 known facts, 680 stored, $3.92** | Pydantic schema: company, metric (PUE, emissions, water...), value, unit, year, page → `facts` table | Structured extraction |
| 7 | Small agent ✅ **93% vs 67% for the pipeline** | Tools: `search_docs`, `query_facts` (SQL), `calculator`. Compare with the plain pipeline | Tool-calling agents |
| 8 | Run the full eval ✅ | Retrieval hit@5, answer accuracy, not-found accuracy, pipeline vs agent | Evaluation |
| 9 | Cost + reliability ✅ **1.7¢ and 4s per question** | Tokens, $, latency per question; retries, timeouts | Cost, latency, failure modes |
| 10 | Ship it | FastAPI + simple page, Dockerfile, GitHub Actions running tests on every push | Full-stack, CI/CD |
| 11 | README | Results, failures, cost per query, next steps | Communication |
