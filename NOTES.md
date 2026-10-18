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

## Plan

| # | Step | What gets built | Capability |
|---|---|---|---|
| 1 | Documents ✅ | 10 reports + manifest | – |
| 2 | Eval questions ✅ | 30 checked questions | Evaluation |
| 3 | Ingest + chunk into **SQLite** | `documents` and `chunks` tables (company, report year, page) | Structured, queryable data; SQL |
| 4 | Retrieval | Embeddings + keyword search, filter by company/year first | Retrieval |
| 5 | Answer with citations | Answers only from retrieved text, cites report + page, otherwise "not found" | Retrieval, failure modes |
| 6 | Structured extraction | Pydantic schema: company, metric (PUE, emissions, water...), value, unit, year, page → `facts` table | Structured extraction |
| 7 | Small agent | Tools: `search_docs`, `query_facts` (SQL), `calculator`. Compare with the plain pipeline | Tool-calling agents |
| 8 | Run the full eval | Retrieval hit@5, answer accuracy, not-found accuracy, pipeline vs agent | Evaluation |
| 9 | Cost + reliability | Tokens, $, latency per question; retries, timeouts | Cost, latency, failure modes |
| 10 | Ship it | FastAPI + simple page, Dockerfile, GitHub Actions running tests on every push | Full-stack, CI/CD |
| 11 | README | Results, failures, cost per query, next steps | Communication |
