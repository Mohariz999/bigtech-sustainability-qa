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

Results: `facts` table in data/corpus.sqlite, a copy in eval/results/facts.csv,
and every call (tokens, cost, latency, rejections) in logs/extraction.jsonl.

Run:  python extract.py --dry-run      # list pages + cost estimate, no API calls
      python extract.py --limit 5      # try 5 pages
      python extract.py                # all candidate pages
"""
import argparse
import collections
import csv
import json
import pathlib
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from answer import MODEL, PRICES, cost, load_dotenv, numbers_in

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
    pages = candidate_pages(db)[:args.limit]
    per_doc = collections.Counter(p["doc_id"] for p in pages)
    print(f"{len(pages)} candidate pages: " + ", ".join(f"{d} {n}" for d, n in sorted(per_doc.items())))
    print(f"Estimated cost on {MODEL}: about ${estimate(pages):.2f}")
    if args.dry_run:
        return

    extractor = Extractor()
    (ROOT / "logs").mkdir(exist_ok=True)
    logs = []
    with ThreadPoolExecutor(WORKERS) as pool, open(ROOT / "logs" / "extraction.jsonl", "w") as logfile:
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

    db.executescript("DROP TABLE IF EXISTS facts;" + FACTS_SCHEMA)
    kept = [f for log in logs for f in log["kept"]]
    db.executemany("INSERT INTO facts (company, doc_id, page, metric, value, unit, period, basis, quote) "
                   "VALUES (:company, :doc_id, :page, :metric, :value, :unit, :period, :basis, :quote)", kept)
    db.commit()
    (ROOT / "eval" / "results").mkdir(exist_ok=True)
    with open(ROOT / "eval" / "results" / "facts.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["company", "doc_id", "page", "metric", "value", "unit", "period", "basis", "quote"])
        w.writeheader()
        w.writerows(sorted(kept, key=lambda r: (r["company"], r["metric"], r["period"], r["doc_id"], r["page"])))

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
