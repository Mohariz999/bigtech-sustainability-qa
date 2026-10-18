"""Score the full pipeline (search + Claude) on all 30 eval questions.

Per question:
- answerable: correct if the answer contains the expected values (must_contain: all of them;
  must_contain_any: at least one), ignoring case and thousands separators.
  Citation correct if any cited (doc_id, page) is one of the question's gold pages.
- not_found: correct only if the system said not_found.
- grounding problems (quote not in passage, number not in passage) are counted separately.

Writes eval/results/answers.md (summary + every failure) and logs/answers.jsonl
(one full record per question: retrieved pages, answer, citations, tokens, cost, latency).

Run:  python eval/run_answers.py --dry-run     # estimate cost, no API calls
      python eval/run_answers.py --limit 3     # try 3 questions first
      python eval/run_answers.py               # all 30
      python eval/run_answers.py --rescore     # re-score the saved log after editing the answer key (free)
"""
import argparse
import json
import pathlib
import statistics
import sys

import anthropic

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from answer import MODEL, PRICES, SYSTEM, TOP_K, Answerer, format_passages  # noqa: E402
from search import Searcher, company_in, year_in  # noqa: E402

OUT = ROOT / "eval" / "results"
LOGS = ROOT / "logs"


def norm(s: str) -> str:
    return s.lower().replace(",", "")


def is_correct(q: dict, r: dict) -> bool:
    if q["category"].startswith("not_found"):
        return r["status"] == "not_found"
    if r["status"] != "answered":
        return False
    ans = norm(r["answer"])
    if "must_contain" in q:
        return all(norm(x) in ans for x in q["must_contain"])
    return any(norm(x) in ans for x in q["must_contain_any"])


def cites_gold(q: dict, r: dict) -> bool:
    gold = {(s["doc_id"], p) for s in q.get("sources", []) for p in s["pages"]}
    return any((c["doc_id"], c["page"]) in gold for c in r["citations"])


def dry_run(questions: list[dict]) -> None:
    """Rough cost estimate (~4 characters per token) before spending anything."""
    s = Searcher()
    in_tok = []
    for q in questions:
        hits = s.hybrid(q["question"], TOP_K, company=company_in(q["question"]), data_year=year_in(q["question"]))
        in_tok.append((len(SYSTEM) + len(format_passages(hits)) + len(q["question"])) / 4 + 300)
    out_tok = 600  # answer JSON plus thinking, a rough allowance
    inp, out = PRICES[MODEL]
    total = sum(t * inp + out_tok * out for t in in_tok) / 1e6
    print(f"{len(questions)} questions, ~{statistics.mean(in_tok):.0f} input tokens each, "
          f"~{out_tok} output tokens allowed for.\nEstimated cost on {MODEL}: about ${total:.2f} "
          f"(${total/len(questions):.4f} per question). Real numbers are logged per call.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--rescore", action="store_true")
    args = ap.parse_args()

    questions = [json.loads(l) for l in open(ROOT / "eval" / "questions.jsonl") if l.strip()][:args.limit]
    if args.dry_run:
        return dry_run(questions)
    if args.rescore:
        return rescore(questions)

    answerer = Answerer()
    LOGS.mkdir(exist_ok=True)
    OUT.mkdir(exist_ok=True)
    rows = []
    with open(LOGS / "answers.jsonl", "w") as log:
        for q in questions:
            try:
                r = answerer.answer(q["question"])
            except anthropic.AuthenticationError:
                sys.exit("The API rejected the key (401). Check ANTHROPIC_API_KEY in .env: it should be "
                         "the full key from console.anthropic.com, starting with sk-ant-.")
            except anthropic.APIError as e:  # still failing after the SDK's retries: record it, keep going
                r = {"question": q["question"], "status": "error", "answer": "", "citations": [], "calls": [],
                     "retrieved": [], "problems": [f"API error: {type(e).__name__}"], "cost_usd": 0.0, "latency_s": 0.0}
            r.update(id=q["id"], category=q["category"], expected=q["answer"],
                     correct=is_correct(q, r), cites_gold=cites_gold(q, r))
            log.write(json.dumps(r) + "\n")
            rows.append((q, r))
            print(f"{q['id']} {'✓' if r['correct'] else '✗'} [{r['status']}] ${r['cost_usd']:.4f} "
                  f"{r['latency_s']}s  {r['answer'][:90]}")
    write_report(rows)


def rescore(questions: list[dict]) -> None:
    """Re-apply the (possibly edited) answer key to the saved answers. No API calls."""
    saved = {r["id"]: r for r in map(json.loads, open(LOGS / "answers.jsonl"))}
    rows = []
    for q in questions:
        r = saved[q["id"]]
        r.update(expected=q["answer"], correct=is_correct(q, r), cites_gold=cites_gold(q, r))
        rows.append((q, r))
    with open(LOGS / "answers.jsonl", "w") as log:
        log.writelines(json.dumps(r) + "\n" for _, r in rows)
    write_report(rows)


def write_report(rows: list[tuple[dict, dict]]) -> None:
    ans = [(q, r) for q, r in rows if not q["category"].startswith("not_found")]
    nf = [(q, r) for q, r in rows if q["category"].startswith("not_found")]
    lat = sorted(r["latency_s"] for _, r in rows)
    costs = [r["cost_usd"] for _, r in rows]
    tokens_in = sum(c["input_tokens"] for _, r in rows for c in r["calls"])
    tokens_out = sum(c["output_tokens"] for _, r in rows for c in r["calls"])
    pct = lambda xs: f"{sum(xs)}/{len(xs)} ({sum(xs)/len(xs):.0%})" if xs else "n/a"

    summary = [
        ("Answer correct (answerable)", pct([r["correct"] for _, r in ans])),
        ("Cites a correct page (answerable)", pct([r["cites_gold"] for _, r in ans])),
        ("Says not found when it should", pct([r["correct"] for _, r in nf])),
        ("Overall correct", pct([r["correct"] for _, r in rows])),
        ("Answers with a grounding problem", pct([bool(r["problems"]) for _, r in rows])),
        ("Retries / errors", f"{sum(len(r['calls']) > 1 for _, r in rows)} / {sum(r['status'] == 'error' for _, r in rows)}"),
        ("Total cost", f"${sum(costs):.4f} (${statistics.mean(costs):.4f} per question)"),
        ("Tokens in / out", f"{tokens_in:,} / {tokens_out:,}"),
        ("Latency median / p95 / max", f"{statistics.median(lat):.1f}s / {lat[int(0.95 * (len(lat) - 1))]:.1f}s / {lat[-1]:.1f}s"),
    ]
    with open(OUT / "answers.md", "w") as f:
        f.write(f"# Answer results\n\n{len(rows)} questions, model `{MODEL}`, top {TOP_K} passages. "
                "Generated by `eval/run_answers.py`.\n\n| Metric | Result |\n|---|---|\n")
        f.writelines(f"| {k} | {v} |\n" for k, v in summary)
        f.write("\n## Failures and flags\n\n")
        for q, r in rows:
            if not r["correct"] or r["problems"] or (not q["category"].startswith("not_found") and not r["cites_gold"]):
                f.write(f"- **{q['id']}** [{q['category']}] {'✗ wrong' if not r['correct'] else '✓ right but flagged'}  \n"
                        f"  Q: {q['question']}  \n  Got [{r['status']}]: {r['answer'] or '-'}  \n"
                        f"  Expected: {q['answer']}  \n  Cited: {[f'{c['doc_id']} p{c['page']}' for c in r['citations']]}; "
                        f"retrieved: {r['retrieved']}  \n  Problems: {r['problems'] or 'none'}\n")
    print("\n" + "\n".join(f"{k}: {v}" for k, v in summary))
    print(f"\nWrote {(OUT / 'answers.md').relative_to(ROOT)} and {(LOGS / 'answers.jsonl').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
