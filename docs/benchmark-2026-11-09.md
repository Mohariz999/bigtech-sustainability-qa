# Matched live benchmark · 9 November 2026 (Singapore time)

The pipeline scored **20/30 → 21/30**, and the agent **25/30 → 26/30**, under the same deterministic-v2 grader. Both gains are entirely q28 changing structured status from `answered` to `not_found`. The answer bodies remain similar and still discuss Google's nearby projection. This is a narrow status-based improvement in the measured score; it does **not** establish a general semantic accuracy improvement or a causal benefit from a specific engineering fix.

## Controlled comparison

- Baseline: `0aeb7785b462d0589b371ebf15b7f008cc6de7c2`; final: `fc2031bc765e1c5ea4e25ce085d1b6462db2f208`.
- Same 30 development questions and answer keys; SHA256 `156e66fa7d2a0d5cc525e02bca9560870be05a4fbf7e414b253ac9f26fe30f89`.
- All 120 fresh outputs graded offline with final `deterministic-v2`, default absolute/relative numerical tolerance zero. Grader SHA256 `984299e2ed27b23cafaa373459abf48fc35abd60bf7b6372690df4097b200499`. No retrospective answer-key edits or model judge.
- Same existing corpus: 10 documents, 2,787 chunks, 680 original facts, source metadata and PDFs; no extraction or document embedding regeneration. Both staged SQLite copies SHA256 `46c6189aff897f1847269923eda1b0fee944c8817c7c71233b1c44cdcd8d954f`.
- Same restored BGE snapshot, Python environment, Anthropic SDK **1.15.0**, configured `claude-opus-5-5`, medium effort. All 211 returned calls used that model; no fallback observed.
- Original generation budgets retained: pipeline 4,000 output tokens; agent 8,000 per call and 8 model steps. Automatic SDK retries disabled symmetrically for budget safety.
- Execution order: baseline pipeline, final pipeline, baseline agent, final agent. Free token counting and conservative reservations preceded paid requests; no repeated paid trials.

## Accuracy and resources

Costs below are totals for 30 questions, calculated from actual API usage, not a provider invoice. Input excludes cache tokens; all cache reads/writes were zero. Latencies are medians per question; model time sums that question's model requests. Application time excludes the added free budget token-count requests.

| Run | Correct | Input tokens | Output tokens | Model calls | Cost USD | Median model API s | Median application s |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline pipeline | 20/30 (66.7%) | 85,416 | 8,617 | 30 | $0.514004 | 3.65 | 3.68 |
| Final pipeline | 21/30 (70.0%) | 85,416 | 8,460 | 30 | $0.510864 | 3.29 | 3.33 |
| Baseline agent | 25/30 (83.3%) | 236,648 | 15,632 | 74 | $1.259232 | 7.12 | 7.17 |
| Final agent | 26/30 (86.7%) | 291,448 | 16,422 | 77 | $1.494232 | 6.56 | 6.60 |

**Total: $3.778332 of the approved $6**, 698,928 input tokens, 49,131 output tokens, 211 paid model calls, zero run errors and zero outstanding budget reservations. Final-only pipeline plus agent cost $2.005096. Usage pricing: $4/million input, $20/million output on standard Opus 5.5; see [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing). [Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting) is free. No additional paid calls are needed for this report.

Pipeline cost fell $0.003140 (0.6%). Agent cost rose **$0.235000 (18.7%)**, with 54,800 additional input tokens (23.2%), 790 additional output tokens and three additional model calls. Lower median latency was observed in both runs, but one ordered trial cannot establish a reproducible speed improvement. Median observed application time including budget checks was 4.00/3.67 seconds for baseline/final pipeline and 8.00/7.32 seconds for baseline/final agent. Native latency fields include those checks; use the explicit normalized fields for comparisons.

## Per-question evidence and engineering impact

**q28 is the only changed correctness verdict in either system.** It asks for an IEA projection absent from the supplied reports. Both baselines caveat the missing attribution but return `answered`; both finals return `not_found`. All four nevertheless discuss Google's related 2030 projection. The grader rewards the final status; this does not prove that the final prose is materially better. Correct abstentions rose 3/4 → 4/4 in both systems, with no other correctness regressions or gains.

The engineering changes add derived fact provenance/unit/scope/basis qualifications without replacing originals, partial evidence diagnostics, bounded agent/error handling, versioned grading, reproducibility manifests and corrected cache pricing. These capabilities are supported by offline regressions, but the live comparison does **not** isolate their causal contribution. Pipeline input totals are identical and its generation/retrieval path is effectively unchanged; model variation remains a plausible explanation. No ablations or repeated trials were performed. Cache accounting fixes had no monetary effect in these runs because cache token usage was zero.

The agent cited a gold document/page on **23/26 → 22/26** answerable questions; the pipeline stayed **19/26 → 19/26**. This metric checks page overlap, not semantic support. Native diagnostics flagged 3 → 12 agent answers and 3 → 5 pipeline answers. Validator behavior changed, so flag totals cannot be compared as hallucination rates. Some final flags concern calculated quantities absent verbatim from quotations; semantic correctness remains unverified.

## Remaining failures and rubric limitations

The final agent misses **q04, q22, q25, q26** under the frozen rules:

| Question | Observed answer / unresolved issue |
|---|---|
| q04 | Calculates water increase as 33.6%; key requires 34%. A rounding-sensitive failure; score retained. |
| q22 | Leads with 57.5% on management's emissions basis, also gives 61.7% on another series. Key accepts 23.4 or 62; basis selection and strict rounding both matter. It should not be treated as purely a grader defect. |
| q25 | Abstains on which company signed more deals because Microsoft gives capacity rather than a deal count. Discusses Google cumulative 35 GW and Microsoft up to 40 GW; does not give the key's requested answered comparison including 12 and 40. Annual/cumulative and count/capacity ambiguity remains. |
| q26 | Calculates 38.4% using the latest report's restated baseline, also 37.6% using the earlier report. Key accepts 38 or 37 with zero tolerance; boundary-aware v2 rejects these decimals. Score retained, without manually adjusted accuracy. |

The final pipeline misses nine questions. Each reason below describes what the
saved output omitted or reported.

| Question | Observed answer / unresolved issue |
|---|---|
| q05 | Reports growth and a Scope 3 subtotal, but omits the requested 14.5 MtCO2e total. |
| q06 | Uses the prior report's contracted capacity and omits the newer 40 GW figure. |
| q15 | Gives the start date for one Apple facility and abstains on the company-wide start date. |
| q16 | Reports growth and carbon-free energy share, but abstains on absolute data-centre consumption. |
| q19 | Reports Microsoft's combined emissions and abstains on the market-based Scope 2 change. |
| q20 | Reports 66% from the older edition instead of the latest 65% value. |
| q23 | Explains annual 100% renewable matching but omits the 65% hourly figure. |
| q25 | Abstains on deal counts and mixes cumulative capacity with a single partnership. |
| q26 | Gives older total electricity consumption and abstains on data-centre-specific growth. |

The same misses occur in both versions for each system. The raw outputs and
grading reasons distinguish missing evidence from incomplete or mis-scoped
answers. The benchmark does not measure unit-aware extraction accuracy.

## Historical results and scope

The preserved historical **90% agent / 63% pipeline** scores used legacy-v1 substring grading and earlier saved generations. They are **not directly comparable** to this common-v2 live comparison. In particular substring matching could accept 37.6 as containing 37, whereas v2 does not. Historical files remain unchanged; no claim is made that current agent accuracy fell from 90% to 86.7% on a common rubric.

This is one run per system/version on 30 development questions, not a held-out enterprise benchmark. Model nondeterminism, ordered execution, small sample size, strict numerical keys and partial semantic validation limit conclusions. The measured +1 question does not establish statistical significance. Future rubric revisions should be versioned and applied equally to all preserved outputs; any new paid runs require separate approval.

## Artifacts and validation

The [published verdicts and metrics](../eval/results/benchmark-2026-11-09/) record
every question's status and grade, run totals, frozen settings and input hashes.
The full answers, citations, tool traces and request ledger are retained locally.

Run `python -m eval.verify_benchmark` to validate the published verdicts, token
costs and README figures. Before publication, an offline regrade of all 120 raw
outputs reproduced these grades, and all 211 requests reconciled to the recorded
total. Regrading and recalculating latency medians require the full local evidence.
Live generation requires the frozen corpus and model environment described above.
