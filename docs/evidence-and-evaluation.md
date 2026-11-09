# Evidence diagnostics, evaluation and agent reliability

This phase introduces no new measured model accuracy. The historical answer logs,
question set, gold facts, safety cases and result reports are unchanged. All added
verification uses deterministic code, fake clients and saved artifacts.

## Evidence diagnostics

`evidence_validation` separates structural citation checks from partial deterministic
checks of company, structured metric/reporting period, units and explicit source
attribution flags. Semantic support is always labeled `not_verified`. No LLM judge
or external verification service is called, enabled or required.

Empty or nonmatching quotations no longer supply numeric evidence. Whitespace and
apostrophes are normalized on both sides. Numeric checks recognize signs, scientific
notation, rounding and explicit compatible unit conversions. Bare matching numbers
still cannot prove a claim's association to a particular company, metric, scope or
year. Company and metric diagnostics are narrow lexical checks and can produce false
positives; explanatory mentions of another company are one example. Period checks
require an explicit single-year question and structured fact metadata, not an
inferred publication year. A calculator result is evidence of arithmetic, not of
the correctness or compatibility of its operands.

The `problems` list remains diagnostic: these changes do not turn every flagged
answer into a refusal. Existing clients retain the `status`, `answer`, `citations`,
`calls`, `cost_usd`, `latency_s` and `problems` fields.

## Grading versions

- `legacy-v1` preserves the historical substring rules for interpreting old scores.
- `deterministic-v2` uses complete numeric boundaries, simple assertion/rejection
  handling and explicit compatible units. Default absolute and relative tolerances
  are both **zero**. No additional rounding allowance is silently applied to the
  fixed historical answers.
- Future `numeric_checks` can specify a value, unit, `atol` and `rtol` explicitly.
  Absolute tolerance is expressed in the expected unit. Such criteria are additional
  to the existing requirements; changing them creates a different evaluation and
  must not be presented as an improvement in model accuracy.

The extraction scorer also labels its historical raw-number recall separately and
only reports unit-aware numeric validation when gold units are explicit. The
existing gold facts lack those units, so their unit-aware status remains unverified.
No gold units are invented from the extracted candidate being tested.

The question file is not rewritten. Legacy bare-number requirements still lack
units/association information; v2 cannot recover omitted meaning from them. Simple
negation detection is not a general natural-language judge. Both grading versions
can accept semantically wrong answers and require per-question review. The 30-case
set has been used during development and is not an untouched held-out test set.

## Offline use

```bash
# Read-only estimate from saved costs; no embedding/model client is loaded.
.venv/bin/python eval/run_answers.py --dry-run --system agent

# Rescore saved outputs into a new run directory. No generation or paid calls.
.venv/bin/python eval/run_answers.py --system agent --rescore --grading legacy-v1
.venv/bin/python eval/run_answers.py --system agent --rescore --grading deterministic-v2

# A subset does not truncate the original log.
.venv/bin/python eval/run_answers.py --rescore --limit 3

# Optional explicit saved-log source.
.venv/bin/python eval/run_answers.py --rescore --source-log /path/to/saved.jsonl
```

New answer/safety evaluations and rescoring, plus offline extraction/retrieval
reports, write under
`logs/evaluation-runs/<unique-id>/`. A manifest records the code revision, dirty
working-tree status, actual Python source hashes, Python version, requirements
hash, dataset hash, relevant configuration and source-log hash for rescoring.
Answer runs record selected question IDs. A database file hash is recorded when
present without an active WAL, explicitly labeled as **not a locked snapshot**.
A manifest cannot prevent concurrent corpus edits: freeze/backup corpus artifacts
before controlled live comparisons.

Per-question records retain actual response model IDs, input/output/cache tokens,
cost, model-call latency, retrieval and citation information, abstention, grading
version and failure reasons. End-to-end latency additionally measures retrieval,
tools and validation for new outputs. Historical logs lack this measurement and
reports say so. Unknown response model rates use the configured fallback estimate
and are explicitly marked `cost_is_estimate`; do not treat that number as verified
billing. API errors preserve successful prior calls and mark measurements incomplete.
Reports show every question, including successes, alongside failures and flags.

## Agent safeguards

The hand-written loop is retained. Local tool validation enforces exact keys, string
types, allowed companies/metrics and a four-digit year or `any`; parameterized SQL
and the calculator restrictions remain. Expected database/local-retrieval errors
become normal tool errors, with programming defects left visible.

Defaults: eight model-loop steps, 24 total tools, two identical executions per
argument set, 24,000 characters per tool result and 160,000 characters of serialized
conversation context. These are character budgets, not token guarantees. SDK retries
remain separately configured and may perform multiple network attempts per step.
Oversized results do not register unseen sources; duplicate tool-use IDs stop the
loop. Diagnostics include a request ID, tool outcomes, tool timing and explicit
termination reasons. No external monitoring or additional dependency is introduced.

Instruction-like fact quotations are screened using the existing pattern layer,
as search passages already were. This is incomplete defense; poisoned numerical
facts without instruction words may still pass. Safety records now distinguish
planted cases, search calls and screen withholding. Historical unexposed cases must
not be presented as successful defense against an attack the agent never saw.

## Final live comparison — approval required

Paid extraction, generation and evaluation remain prohibited until the user
explicitly approves a concrete run after API credits are available. Approval to
merge engineering work is not approval to spend API credits or publish new numerical
performance claims.

At the final stage:

1. Freeze the original baseline revision (`0aeb778`) and the final approved revision,
   questions, grading version/criteria, source PDFs and corpus snapshots. Record
   hashes and effective model configuration; distinguish intended fact-quality
   changes from unrelated corpus drift.
2. Apply exactly the same fixed questions and grading code/criteria to both versions.
   If historical outputs are rescored, label them **historical outputs rescored**,
   not a freshly generated baseline. Old substring-based scores remain a separate
   historical series.
3. For a controlled before/after generation comparison, run both revisions with the
   same requested model/settings and comparable conditions. Record actual fallbacks.
   Repeated paired runs would help quantify stochastic variability; propose their
   extra cost separately before running them.
4. Report per-question correctness, retrieval, citation validity, abstention,
   failures, input/output/cache tokens, model-call and end-to-end latency where
   available, cost and any incomplete measurements. Report small sample size,
   grading limitations and model variability.
5. Attribute an improvement to a particular fix only when per-question evidence
   supports it. A higher aggregate score alone does not establish causation; an
   ablation or controlled targeted comparison may be necessary.
6. After explicit approval and completed measurement, update README with actual
   results, concise before/after comparison, known limitations and remaining
   failures. Report lower or unchanged accuracy transparently. Do not substitute
   changed grading rules for a measured engineering improvement.

The completed matched comparison is documented in the [benchmark report](benchmark-2026-11-09.md).
