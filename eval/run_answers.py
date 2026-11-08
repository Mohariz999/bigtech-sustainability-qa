"""Evaluate answers with versioned grading and immutable run artifacts.

Default deterministic-v2 uses numeric boundaries and explicit compatible units;
legacy-v1 preserves historical substring grading. Citation, retrieval, abstention
and evidence diagnostics remain separate from answer correctness.

Runs write reports, every question record and a provenance manifest under
logs/evaluation-runs/<unique-id>/; historical logs/results are never overwritten.

Offline: --dry-run estimates from saved costs without loading models;
--rescore applies the selected grading version to saved outputs, without generation.
Default execution generates live answers and requires API credits and user approval.
See docs/evidence-and-evaluation.md for limitations and the comparison protocol.
"""
import argparse
import json
import pathlib
import statistics
import sys

import anthropic

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from answer import MODEL, PRICES, CACHE_READ_MULTIPLIERS, CACHE_WRITE_MULTIPLIERS, SYSTEM, TOP_K, Answerer, format_passages  # noqa: E402
from search import Searcher, company_in, year_in  # noqa: E402
from eval.grading import VERSION, grade
from diagnostics import create_run

OUT = ROOT / "eval" / "results"
LOGS = ROOT / "logs"
NAME = "answers"  # output file name; "answers_agent" for --system agent


def norm(s: str) -> str:
    return s.lower().replace(",", "")


def is_correct(q: dict, r: dict) -> bool:
    return grade(q, r)["correct"]


def cites_gold(q: dict, r: dict) -> bool:
    gold = {(s["doc_id"], p) for s in q.get("sources", []) for p in s["pages"]}
    return any((c["doc_id"], c["page"]) in gold for c in r["citations"])


def retrieval_success(q, r):
    gold = {f"{source['doc_id']} p{page}" for source in q.get("sources", []) for page in source["pages"]}
    return bool(gold.intersection(r.get("retrieved", [])))


def dry_run(questions: list[dict]) -> None:
    """Estimate from recorded usage only; never load embeddings or a model client."""
    path = LOGS / f"{NAME}.jsonl"
    saved = [json.loads(line) for line in open(path) if line.strip()]
    mean = statistics.mean(r["cost_usd"] for r in saved)
    print(f"Historical recorded-cost estimate for {len(questions)} questions: ${mean * len(questions):.2f}. "
          "This is not a quote for the updated implementation; no models were loaded.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--grading", choices=["legacy-v1", VERSION], default=VERSION)
    ap.add_argument("--source-log", type=pathlib.Path, help="saved log to rescore; defaults to historical log")
    ap.add_argument("--system", choices=["pipeline", "agent"], default="pipeline")
    args = ap.parse_args()
    global NAME, OUT
    NAME = "answers" if args.system == "pipeline" else "answers_agent"

    if args.limit is not None and args.limit <= 0:
        ap.error("--limit must be positive")
    questions = [json.loads(l) for l in open(ROOT / "eval" / "questions.jsonl") if l.strip()][:args.limit]
    if args.dry_run:
        return dry_run(questions)
    if args.rescore:
        return rescore(questions, source_log=args.source_log, grading=args.grading)

    if args.system == "agent":
        from agent import Agent
        answerer = Agent()
    else:
        answerer = Answerer()
    OUT = new_run(args.system, args.grading, selected_ids=[q["id"] for q in questions])
    rows = []
    with open(OUT / f"{NAME}.jsonl", "x") as log:
        for q in questions:
            try:
                r = answerer.answer(q["question"])
            except anthropic.AuthenticationError:
                sys.exit("The API rejected the key (401). Check ANTHROPIC_API_KEY in .env: it should be "
                         "the full key from console.anthropic.com, starting with sk-ant-.")
            except anthropic.APIError as e:  # still failing after the SDK's retries: record it, keep going
                r = {"question": q["question"], "status": "error", "answer": "", "citations": [], "calls": [],
                     "retrieved": [], "problems": [f"API error: {type(e).__name__}"], "cost_usd": 0.0, "latency_s": 0.0}
            assessment = grade(q, r, args.grading)
            r.update(id=q["id"], category=q["category"], expected=q["answer"],
                     correct=assessment["correct"], grading=assessment, cites_gold=cites_gold(q, r),
                     citation_validity=not any("quote not found" in p or "cites " in p or "without cit" in p for p in r["problems"]),
                     abstained=r["status"] == "not_found", retrieval_success=retrieval_success(q, r))
            log.write(json.dumps(r) + "\n")
            rows.append((q, r))
            print(f"{q['id']} {'✓' if r['correct'] else '✗'} [{r['status']}] ${r['cost_usd']:.4f} "
                  f"{r['latency_s']}s  {r['answer'][:90]}")
    write_report(rows)


def new_run(system, grading, source_log=None, selected_ids=None):
    return create_run(ROOT, ROOT / "eval/questions.jsonl",
                      {"system": system, "grading_version": grading, "requested_model": MODEL,
                       "effort": "medium", "top_k": TOP_K, "fallbacks": "default",
                       "prices": PRICES, "cache_read_multipliers": CACHE_READ_MULTIPLIERS,
                       "cache_write_multipliers": CACHE_WRITE_MULTIPLIERS, "max_model_calls": 8 if system == "agent" else 2,
                       "max_tokens_per_call": 8000 if system == "agent" else 4000,
                       "max_retries": 3, "embedding_model": "BAAI/bge-small-en-v1.5",
                       "selected_question_ids": selected_ids}, source_log=source_log)


def rescore(questions: list[dict], source_log=None, grading=VERSION) -> None:
    """Write a new derived run; never overwrite the source log, even with --limit."""
    global OUT
    source = source_log or LOGS / f"{NAME}.jsonl"
    records = [json.loads(line) for line in open(source) if line.strip()]
    saved = {r["id"]: r for r in records}
    if len(saved) != len(records) or any(q["id"] not in saved for q in questions):
        raise ValueError("duplicate or missing question IDs in source log")
    OUT = new_run("agent" if NAME == "answers_agent" else "pipeline", grading, source, [q["id"] for q in questions])
    rows = []
    for q in questions:
        r = dict(saved[q["id"]])
        assessment = grade(q, r, grading)
        r.update(expected=q["answer"], correct=assessment["correct"], grading=assessment,
                 cites_gold=cites_gold(q, r), retrieval_success=retrieval_success(q, r),
                 result_kind="rescore_saved_outputs")
        rows.append((q, r))
    with open(OUT / f"{NAME}.jsonl", "x") as log:
        log.writelines(json.dumps(r) + "\n" for _, r in rows)
    write_report(rows)


def write_report(rows: list[tuple[dict, dict]]) -> None:
    ans = [(q, r) for q, r in rows if not q["category"].startswith("not_found")]
    nf = [(q, r) for q, r in rows if q["category"].startswith("not_found")]
    lat = sorted(r["latency_s"] for _, r in rows)
    costs = [r["cost_usd"] for _, r in rows]
    tokens_in = sum(c["input_tokens"] for _, r in rows for c in r["calls"])
    tokens_out = sum(c["output_tokens"] for _, r in rows for c in r["calls"])
    measured_e2e = [r["end_to_end_latency_s"] for _, r in rows if "end_to_end_latency_s" in r]
    pct = lambda xs: f"{sum(xs)}/{len(xs)} ({sum(xs)/len(xs):.0%})" if xs else "n/a"

    summary = [
        ("Answer correct (answerable)", pct([r["correct"] for _, r in ans])),
        ("Retrieved a gold page (answerable)", pct([retrieval_success(q, r) for q, r in ans])),
        ("Cites a correct page (answerable)", pct([r["cites_gold"] for _, r in ans])),
        ("Says not found when it should", pct([r["correct"] for _, r in nf])),
        ("Overall correct", pct([r["correct"] for _, r in rows])),
        ("Answers with a grounding problem", pct([bool(r["problems"]) for _, r in rows])),
        # The agent makes several model calls by design, so "more than one call" only means a retry for the pipeline.
        (("Model calls per question (mean / max)", f"{statistics.mean(len(r['calls']) for _, r in rows):.1f} / "
          f"{max(len(r['calls']) for _, r in rows)}") if NAME == "answers_agent" else
         ("Retries", f"{sum(len(r['calls']) > 1 for _, r in rows)}")),
        ("End-to-end latency median", f"{statistics.median(measured_e2e):.2f}s" if measured_e2e else "not recorded in historical outputs"),
        ("Cost using estimated unknown-model rates", str(any(c.get("cost_is_estimate", False) for _, r in rows for c in r["calls"]))),
        ("Incomplete token/cost measurements", str(sum(bool(r.get("measurement_incomplete")) for _, r in rows))),
        ("Errors", f"{sum(r['status'] == 'error' for _, r in rows)}"),
        ("Total cost", f"${sum(costs):.4f} (${statistics.mean(costs):.4f} per question)"),
        ("Cache tokens read / created", f"{sum(c.get('cache_read_tokens', 0) for _, r in rows for c in r['calls'])} / "
         f"{sum(c.get('cache_creation_tokens', 0) for _, r in rows for c in r['calls'])}"),
        ("Tokens in / out", f"{tokens_in:,} / {tokens_out:,}"),
        ("Latency median / p95 / max", f"{statistics.median(lat):.1f}s / {lat[int(0.95 * (len(lat) - 1))]:.1f}s / {lat[-1]:.1f}s"),
    ]
    with open(OUT / f"{NAME}.md", "w") as f:
        system = "tool-calling agent (agent.py)" if NAME == "answers_agent" else f"pipeline (answer.py), top {TOP_K} passages"
        actual_models = sorted({c["model"] for _, r in rows for c in r["calls"]})
        versions = sorted({r.get("grading", {}).get("version", "legacy-v1") for _, r in rows})
        f.write(f"# Answer results: {system}\n\n{len(rows)} questions. Actual recorded models: {actual_models}. "
                f"Grading: {versions}. See manifest.json for code and dataset identity. "
                "Rescoring saved outputs is not a new live benchmark. Deterministic checks do not prove semantic correctness.\n\n"
                "| Metric | Result |\n|---|---|\n")
        f.writelines(f"| {k} | {v} |\n" for k, v in summary)
        f.write("\n## Every question\n\n| ID | Correct | Cites gold | Status | Tokens in/out | Recorded cost | Model-call latency |\n"
                "|---|---|---|---|---|---|---|\n")
        for q, r in rows:
            tin = sum(c["input_tokens"] for c in r["calls"])
            tout = sum(c["output_tokens"] for c in r["calls"])
            f.write(f"| {q['id']} | {r['correct']} | {r['cites_gold']} | {r['status']} | {tin}/{tout} | "
                    f"${r['cost_usd']:.4f} | {r['latency_s']}s |\n")
        f.write("\n## Failures and flags\n\n")
        for q, r in rows:
            if not r["correct"] or r["problems"] or (not q["category"].startswith("not_found") and not r["cites_gold"]):
                f.write(f"- **{q['id']}** [{q['category']}] {'✗ wrong' if not r['correct'] else '✓ right but flagged'}  \n"
                        f"  Q: {q['question']}  \n  Got [{r['status']}]: {r['answer'] or '-'}  \n"
                        f"  Expected: {q['answer']}  \n  Cited: {[f'{c['doc_id']} p{c['page']}' for c in r['citations']]}; "
                        f"retrieved: {r['retrieved']}  \n  Problems: {r['problems'] or 'none'}; grading reasons: {r.get('grading', {}).get('reasons', [])}\n")
    print("\n" + "\n".join(f"{k}: {v}" for k, v in summary))
    print(f"\nWrote {(OUT / f'{NAME}.md').relative_to(ROOT)} and {(OUT / f'{NAME}.jsonl').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
