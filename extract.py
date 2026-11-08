"""Structured extraction: turn report pages into validated rows in a `facts` table.

For each page that looks like it holds metrics (enough numbers + metric words):
1. Send the page text to Claude with a fixed schema: a list of facts, each with
   metric (from a fixed list), value, unit, period, basis and an exact quote.
2. Validate the reply with Pydantic; invalid -> retry once with the error; still
   invalid -> log the page as failed (never store a guess).
3. Check every fact before storing it:
   - the quote must appear on the page,
   - the value must appear in the quote (numbers compared without commas).
   Facts that fail are rejected and counted, not stored.
4. Company, document and page are added by this code, not by the model.

Results: full successful runs publish into an empty `facts` table in data/corpus.sqlite.
Each run saves facts.csv and extraction.jsonl under logs/extraction-runs/<unique-id>/.
Limited/failed runs never replace facts; historical eval/results and logs stay intact.

Run:  python extract.py --dry-run      # list pages + cost estimate, no API calls
      python extract.py --limit 5      # try 5 pages
      python extract.py                # all candidate pages
"""
import argparse
import collections
import csv
import json
import math
import pathlib
import re
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from answer import MODEL, PRICES, cost, cache_usage, cost_is_estimate, load_dotenv, numbers_in
from fact_quality import atomic, migrate, quality_for

ROOT = pathlib.Path(__file__).parent
DB_PATH = ROOT / "data" / "corpus.sqlite"
EFFORT = "medium"
WORKERS = 4                 # pages processed in parallel
MIN_NUMBERS = 20            # a candidate page has at least this many numbers...
MIN_KEYWORDS = 3            # ...and at least this many metric words
KEYWORDS = re.compile(r"\b(PUE|power usage effectiveness|MWh|GWh|TWh|tCO2e|CO2e|scope [123]|water|replenish"
                      r"|renewable|carbon-free|CFE|GW)\b", re.I)

Metric = Literal[
    "pue", "datacenter_electricity", "total_electricity", "total_emissions", "scope1_emissions",
    "scope2_market_emissions", "scope2_location_emissions", "scope3_emissions", "water_consumption",
    "water_replenished", "renewable_electricity_pct", "carbon_free_energy_pct", "clean_energy_contracted",
]
METRICS = list(Metric.__args__)

SYSTEM = f"""You extract sustainability metrics from one page of a company's sustainability report.

Return every value on the page for these metrics, about the reporting company's own operations:
{", ".join(METRICS)}

Rules:
- Only values explicitly written on the page. Never calculate, convert or estimate.
- Skip targets, goals and forecasts (e.g. "a 40% reduction target by 2030"); extract only reported actuals.
- period: the year the value describes, as written (e.g. "2025", "FY25"). For a table, read it from the column header.
- unit: as written (e.g. "MWh", "mtCO2e", "%", "ratio" for PUE, "GW", "million cubic meters").
- basis: the accounting basis if the page states one (e.g. "market-based", "location-based",
  "ambition-based", "management's criteria"), otherwise "".
- quote: a short exact copy of the text the value comes from (under 40 words). For a table row,
  copy the row label and its numbers. Copy it character for character: never shorten it with "...".
- If the page has none of these metrics, return an empty list."""


class Fact(BaseModel):
    metric: Metric
    value: float
    unit: str
    period: str
    basis: str
    quote: str


class PageFacts(BaseModel):
    facts: list[Fact]


SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "metric": {"type": "string", "enum": METRICS},
            "value": {"type": "number"},
            "unit": {"type": "string"},
            "period": {"type": "string"},
            "basis": {"type": "string"},
            "quote": {"type": "string"},
        },
        "required": ["metric", "value", "unit", "period", "basis", "quote"],
        "additionalProperties": False,
    }}},
    "required": ["facts"],
    "additionalProperties": False,
}

FACTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    fact_id   INTEGER PRIMARY KEY,
    company   TEXT NOT NULL,
    doc_id    TEXT NOT NULL REFERENCES documents(doc_id),
    page      INTEGER NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL NOT NULL,
    unit      TEXT NOT NULL,
    period    TEXT NOT NULL,
    basis     TEXT NOT NULL,
    quote     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS facts_lookup ON facts(company, metric, period);
"""


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("’", "'")).strip()


def candidate_pages(db: sqlite3.Connection) -> list[dict]:
    pages: dict[tuple, dict] = {}
    rows = db.execute("SELECT c.doc_id, c.page, c.chunk_idx, c.text, d.company FROM chunks c "
                      "JOIN documents d USING (doc_id) ORDER BY c.doc_id, c.page, c.chunk_idx")
    for doc_id, page, idx, text, company in rows:
        p = pages.setdefault((doc_id, page), {"doc_id": doc_id, "page": page, "company": company, "words": []})
        words = text.split()
        p["words"] += words if idx == 0 else words[50:]  # chunks overlap by 50 words: rebuild the page
    out = []
    for p in pages.values():
        text = " ".join(p.pop("words"))
        if len(re.findall(r"\d[\d,.]*", text)) >= MIN_NUMBERS and len(KEYWORDS.findall(text)) >= MIN_KEYWORDS:
            out.append({**p, "text": text})
    return out


def check_fact(f: Fact, page_text: str) -> str | None:
    """Return why a fact is rejected, or None if it passes."""
    quote = normalise(f.quote)
    if quote not in normalise(page_text):
        return "quote not on page"
    # numbers_in() drops thousands separators, so "42,415,800" in the quote matches 42415800.0
    if not any(abs(float(n) - f.value) <= 1e-9 * max(1.0, abs(f.value)) for n in numbers_in(quote)):
        return "value not in quote"
    return None


class Extractor:
    def __init__(self, client: anthropic.Anthropic | None = None):
        load_dotenv()
        self.client = client or anthropic.Anthropic(timeout=120.0, max_retries=3)

    def _call(self, messages):
        return self.client.beta.messages.create(
            model=MODEL, max_tokens=8000, system=SYSTEM, messages=messages,
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )

    def extract_page(self, page: dict) -> dict:
        messages = [{"role": "user", "content": f"Company: {page['company']}\nDocument: {page['doc_id']}, "
                                                f"page {page['page']}\n\n{page['text']}"}]
        log = {"doc_id": page["doc_id"], "page": page["page"], "calls": [], "status": "ok",
               "kept": [], "rejected": []}
        parsed = None
        for attempt in range(2):
            start = time.perf_counter()
            resp = self._call(messages)
            log["calls"].append({"attempt": attempt + 1, "model": resp.model, "stop_reason": resp.stop_reason,
                                 "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                                 "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                                 "cache_creation_tokens": getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
                                 **cache_usage(resp.usage), "cost_is_estimate": cost_is_estimate(resp.model, resp.usage),
                                 "latency_s": round(time.perf_counter() - start, 2),
                                 "cost_usd": round(cost(resp.model, resp.usage), 6)})
            if resp.stop_reason == "refusal":
                log["status"] = "refused"
                break
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                parsed = PageFacts.model_validate_json(text)
                break
            except ValidationError as e:
                log["status"] = f"invalid: {e.errors()[0]['msg']}"
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"That did not match the schema ({log['status']}). "
                                                         "Reply again with valid JSON only."}]
        if parsed is not None:
            log["status"] = "ok"
            for f in parsed.facts:
                reason = check_fact(f, page["text"])
                row = {"company": page["company"], "doc_id": page["doc_id"], "page": page["page"], **f.model_dump()}
                (log["rejected"] if reason else log["kept"]).append({**row, **({"reason": reason} if reason else {})})
        log["cost_usd"] = round(sum(c["cost_usd"] for c in log["calls"]), 6)
        return log


def ensure_empty_facts(db: sqlite3.Connection) -> None:
    """Refuse replacement before an extraction run can spend money."""
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='facts'").fetchone()
    if exists and db.execute("SELECT COUNT(*) FROM facts").fetchone()[0]:
        raise ValueError("Existing facts are preserved. Extract into a separate empty database.")


def publish_facts(db: sqlite3.Connection, logs: list[dict], expected_pages: set[tuple]) -> None:
    """Publish a complete successful extraction to an empty store, atomically.

    Partial/limited or failed runs remain run artifacts only. Never delete old rows.
    Caller owns any surrounding transaction; nested savepoints do not commit it.
    """
    actual = [(log["doc_id"], log["page"]) for log in logs]
    if (not expected_pages or len(actual) != len(set(actual)) or set(actual) != expected_pages
            or any(log["status"] != "ok" for log in logs)):
        raise ValueError("Incomplete or failed extraction; facts were not published")
    with atomic(db):
        ensure_empty_facts(db)
        # executescript implicitly commits: execute the simple DDL statements individually.
        for statement in FACTS_SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        for log in logs:
            for row in log["kept"]:
                if (row["doc_id"], row["page"]) != (log["doc_id"], log["page"]):
                    raise ValueError("fact source does not match extraction page")
                if any(flag.startswith("source_") for flag in quality_for(db, row)["flags"]):
                    raise ValueError("invalid fact source attribution")
                if not math.isfinite(row["value"]):
                    raise ValueError("non-finite fact value")
                db.execute("INSERT INTO facts (company,doc_id,page,metric,value,unit,period,basis,quote) "
                           "VALUES (:company,:doc_id,:page,:metric,:value,:unit,:period,:basis,:quote)", row)
        migrate(db)


def estimate(pages: list[dict]) -> float:
    inp, out = PRICES[MODEL]
    in_tok = sum((len(SYSTEM) + len(p["text"])) / 4 + 50 for p in pages)
    out_tok = 600 * len(pages)  # facts JSON plus thinking, a rough allowance
    return (in_tok * inp + out_tok * out) / 1e6


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    all_pages = candidate_pages(db)
    pages = all_pages[:args.limit]
    per_doc = collections.Counter(p["doc_id"] for p in pages)
    print(f"{len(pages)} candidate pages: " + ", ".join(f"{d} {n}" for d, n in sorted(per_doc.items())))
    print(f"Estimated cost on {MODEL}: about ${estimate(pages):.2f}")
    if args.dry_run:
        return

    ensure_empty_facts(db)
    if not pages:
        raise SystemExit("No candidate pages; no extraction performed")
    extractor = Extractor()
    run_dir = ROOT / "logs" / "extraction-runs" / uuid.uuid4().hex
    run_dir.mkdir(parents=True)
    logs = []
    with ThreadPoolExecutor(WORKERS) as pool, open(run_dir / "extraction.jsonl", "x") as logfile:
        futures = [pool.submit(extractor.extract_page, p) for p in pages]
        for i, fut in enumerate(futures, 1):
            try:
                log = fut.result()
            except anthropic.AuthenticationError:
                raise SystemExit("The API rejected the key (401). Check ANTHROPIC_API_KEY in .env.")
            except anthropic.APIError as e:
                p = pages[i - 1]
                log = {"doc_id": p["doc_id"], "page": p["page"], "calls": [], "status": f"api error: {type(e).__name__}",
                       "kept": [], "rejected": [], "cost_usd": 0.0}
            logfile.write(json.dumps(log) + "\n")
            logs.append(log)
            print(f"[{i}/{len(pages)}] {log['doc_id']} p{log['page']}: {log['status']}, "
                  f"{len(log['kept'])} kept, {len(log['rejected'])} rejected, ${log['cost_usd']:.4f}")

    kept = [f for log in logs for f in log["kept"]]
    with open(run_dir / "facts.csv", "x", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "doc_id", "page", "metric", "value", "unit", "period", "basis", "quote"])
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: (r["company"], r["metric"], r["period"], r["doc_id"], r["page"])))
    if args.limit is not None:
        print("Limited run: artifacts saved, facts table unchanged.")
    else:
        publish_facts(db, logs, {(p["doc_id"], p["page"]) for p in all_pages})
        db.commit()
    print(f"Run artifacts: {run_dir.relative_to(ROOT)} (historical artifacts unchanged)")

    rejected = [f for log in logs for f in log["rejected"]]
    calls = [c for log in logs for c in log["calls"]]
    lat = sorted(c["latency_s"] for c in calls) or [0]
    print(f"\nPages: {len(logs)} ({sum(l['status'] != 'ok' for l in logs)} failed). "
          f"Facts kept: {len(kept)}, rejected: {len(rejected)} "
          f"({collections.Counter(r['reason'] for r in rejected).most_common()}).")
    print(f"Cost: ${sum(l['cost_usd'] for l in logs):.2f}. Tokens in/out: {sum(c['input_tokens'] for c in calls):,} / "
          f"{sum(c['output_tokens'] for c in calls):,}. Call latency median {lat[len(lat)//2]:.1f}s, max {lat[-1]:.1f}s.")


if __name__ == "__main__":
    main()
