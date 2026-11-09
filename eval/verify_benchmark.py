"""Check the published verdicts and totals using only saved public artifacts."""

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

EVAL = Path(__file__).resolve().parent
RESULTS = EVAL / "results" / "benchmark-2026-11-09"
RUNS = {
    "baseline-pipeline": "Baseline RAG",
    "final-pipeline": "Final RAG",
    "baseline-agent": "Baseline agent",
    "final-agent": "Final agent",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    summary = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
    dataset = (EVAL / "questions.jsonl").read_bytes()
    require(hashlib.sha256(dataset).hexdigest() == summary["contract"]["dataset_sha256"],
            "Question set differs from the frozen benchmark")
    questions = {row["id"]: row["category"].startswith("not_found")
                 for row in map(json.loads, dataset.splitlines())}
    require(len(questions) == 30, "Expected 30 unique questions")
    with (RESULTS / "verdicts.csv").open(newline="", encoding="utf-8") as stream:
        verdicts = list(csv.DictReader(stream))
    require(len(verdicts) == 4 * len(questions), "Unexpected number of verdicts")
    readme = (EVAL.parent / "README.md").read_text(encoding="utf-8")
    rates = summary["standard_rates_usd_per_million"]
    misses, statuses = {}, {}
    total = Decimal(0)
    for run, name in RUNS.items():
        rows = [row for row in verdicts if row["run"] == run]
        require([row["question_id"] for row in rows] == list(questions), f"{run}: question IDs")
        metrics = summary["summary"][run]
        misses[run] = [row["question_id"] for row in rows if row["correct"] == "0"]
        statuses[run] = {row["question_id"]: row["status"] for row in rows}
        correct = sum(row["correct"] == "1" for row in rows)
        require(correct == metrics["correct"] == len(rows) - len(misses[run])
                and metrics["total"] == len(rows)
                and correct / len(rows) == metrics["accuracy"]
                and misses[run] == metrics["failed_questions"], f"{run}: grade totals")
        require(sum(row["correct"] == "1" and questions[row["question_id"]] for row in rows)
                == metrics["not_found_correct"], f"{run}: abstention totals")
        require(sum(row["cites_gold"] == "1" and not questions[row["question_id"]] for row in rows)
                == metrics["cites_gold_answerable"], f"{run}: citation totals")
        require(sum(row["status"] == "error" for row in rows) == metrics["errors"]
                and [row["question_id"] for row in rows if row["native_flag"] == "1"]
                == metrics["native_flagged_answers"], f"{run}: error/flag totals")
        require(metrics["cache_read_tokens"] == metrics["cache_creation_tokens"] == 0,
                f"{run}: this comparison assumes no cached usage")
        cost = Decimal(metrics["input_tokens"] * rates["input"]
                       + metrics["output_tokens"] * rates["output"]) / 1_000_000
        require(cost == Decimal(str(metrics["cost_usd"])), f"{run}: token cost")
        total += cost
        resource_row = (f"| {name} | {metrics['input_tokens']:,} / {metrics['output_tokens']:,} "
                        f"| ${cost:.6f} | {metrics['model_api_latency_median_s']:.2f} / "
                        f"{metrics['application_e2e_median_s']:.2f} s |")
        require(resource_row in readme, f"{run}: README resource row")
        print(f"{name}: {correct}/{len(rows)}, ${cost:.6f}")

    for system, name in (("pipeline", "Standard RAG"), ("agent", "AI agent")):
        baseline, final = f"baseline-{system}", f"final-{system}"
        changes = set(misses[baseline]) ^ set(misses[final])
        require(changes == {"q28"} and statuses[baseline]["q28"] == "answered"
                and statuses[final]["q28"] == "not_found", f"{system}: changed verdict")
        grades = [summary["summary"][run] for run in (baseline, final)]
        accuracy = [f"{row['accuracy']:.1%} ({row['correct']}/{row['total']})" for row in grades]
        require(f"| {name} | {accuracy[0]} | {accuracy[1]} |" in readme,
                f"{system}: README accuracy row")
    require(total == Decimal(str(summary["spent_usd"])) and f"${total:.6f}" in readme,
            "Combined cost differs from published total")
    print(f"Verified {len(verdicts)} published verdicts and ${total:.6f} in recorded token costs.")


if __name__ == "__main__":
    main()
