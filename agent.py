"""A tool-calling agent: Claude decides which tools to call, in what order, and when it's done.

Tools (all run by this code, never by the model):
- search_docs(query, company, year)   hybrid search over the report text  -> passages with ids P<n>
- query_facts(company, metric, year)  lookup in the extracted facts table -> rows with ids F<n>
- calculator(expression)              safe arithmetic                     -> results with ids C<n>

The loop (written by hand, not with the SDK's tool runner, so every step is logged
and capped):
  ask Claude -> if it calls tools, run them and send back the results -> repeat,
  at most MAX_STEPS times -> final answer as JSON with citations to source ids.

The final answer gets the same checks as answer.py: citations must point to sources
the tools actually returned, quotes must appear in them, and every number in the
answer must appear in a cited source (a calculator result counts as a source).

Try it: python agent.py "How much did Google's data centre electricity use grow from 2024 to 2025?"
"""
import ast
import json
import operator
import re
import sqlite3
import sys
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from answer import MODEL, cost, load_dotenv, ungrounded_numbers
from extract import METRICS
from search import DB_PATH, Searcher

EFFORT = "medium"
MAX_STEPS = 8          # cost guard: at most 8 model calls per question
COMPANIES = ["Google", "Microsoft", "Meta", "Amazon", "Apple"]

SYSTEM = """You answer questions about company sustainability reports (Google, Microsoft, Meta, Amazon, Apple;
reports published 2025 and 2026, describing 2024 and 2025) using ONLY what your tools return.

Tools:
- query_facts: exact metric values extracted from the reports. Best for numbers, years and comparisons.
- search_docs: passages of report text. Use it for anything query_facts doesn't cover, and to check context.
- calculator: for any arithmetic. Never calculate in your head.

Rules:
- Every fact in your answer must come from a tool result. Never use outside knowledge.
- If the tools don't give the answer, set status to "not_found". A value for a different company,
  metric or year is NOT an answer.
- If sources disagree (different report years, or a different accounting basis), say so and give both.
- If no year is named, give the most recent figure and say which year it describes.
- Final answer: one to three sentences, with numbers and units. Cite the ids (P.., F.., C..) you used,
  each with a short exact quote from that source (for a calculator result, quote the result)."""

TOOLS = [
    {"name": "search_docs", "strict": True,
     "description": "Hybrid keyword + meaning search over the report text. Returns up to 5 passages with ids.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["query", "company", "year"],
                      "properties": {
                          "query": {"type": "string"},
                          "company": {"type": "string", "enum": COMPANIES + ["any"]},
                          "year": {"type": "string", "description": "Year the data describes: '2024', '2025' or 'any'"}}}},
    {"name": "query_facts", "strict": True,
     "description": "Look up extracted metric values. Each row has value, unit, period, basis, source page and quote.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["company", "metric", "year"],
                      "properties": {
                          "company": {"type": "string", "enum": COMPANIES + ["any"]},
                          "metric": {"type": "string", "enum": METRICS},
                          "year": {"type": "string", "description": "'2024', '2025' or 'any'"}}}},
    {"name": "calculator", "strict": True,
     "description": "Evaluate arithmetic with + - * / ** and parentheses, e.g. (42415800-30637100)/30637100*100",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["expression"],
                      "properties": {"expression": {"type": "string"}}}},
]

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["status", "answer", "citations"],
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["source_id", "quote"],
            "properties": {"source_id": {"type": "string"}, "quote": {"type": "string"}}}},
    },
}

def fmt(value: float) -> str:
    """Full precision with thousands separators: 42415800.0 -> '42,415,800', 1.09 -> '1.09'.
    (A first version used '{:g}', which turned 42415800 into '4.24158e+07'. Caught by a test.)"""
    return f"{value:,.12g}"


class SourceCitation(BaseModel):
    source_id: str
    quote: str


class AgentAnswer(BaseModel):
    status: Literal["answered", "not_found"]
    answer: str
    citations: list[SourceCitation]


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


def calculate(expression: str) -> float:
    """Arithmetic only: walks the parsed expression instead of calling eval(), so no code can run."""
    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("only numbers and + - * / ** ( ) are allowed")
    return ev(ast.parse(expression.replace(",", ""), mode="eval").body)


class Agent:
    def __init__(self, searcher: Searcher | None = None, client: anthropic.Anthropic | None = None,
                 db: sqlite3.Connection | None = None):
        load_dotenv()
        self.searcher = searcher or Searcher()
        # Tests pass their own in-memory db, so a fresh checkout (no data/corpus.sqlite) can still run them.
        self.db = db or sqlite3.connect(DB_PATH, check_same_thread=False)
        self.client = client or anthropic.Anthropic(timeout=90.0, max_retries=3)

    # -- tools ---------------------------------------------------------------
    def _search_docs(self, query, company, year, sources):
        hits = self.searcher.hybrid(query, 5, company=None if company == "any" else company,
                                    data_year=None if year == "any" else year)
        out = []
        for h in hits:
            sid = f"P{h.chunk_id}"
            sources[sid] = {"doc_id": h.doc_id, "page": h.page, "text": f"{h.doc_id} page {h.page} {h.text}"}
            out.append(f"[{sid}] ({h.doc_id}, page {h.page})\n{h.text}")
        return "\n\n".join(out) or "No passages found."

    def _query_facts(self, company, metric, year, sources):
        sql = "SELECT fact_id, company, doc_id, page, value, unit, period, basis, quote FROM facts WHERE metric = ?"
        args = [metric]
        if company != "any":
            sql += " AND company = ?"
            args.append(company)
        rows = self.db.execute(sql + " ORDER BY company, period, doc_id LIMIT 40", args).fetchall()
        if year != "any":  # periods are written as '2025', 'FY25', 'FY2025'...
            short = year[-2:]
            rows = [r for r in rows if year in r[6] or re.search(rf"FY\s?{short}\b", r[6], re.I)]
        out = []
        for fid, comp, doc_id, page, value, unit, period, basis, quote in rows:
            sid = f"F{fid}"
            sources[sid] = {"doc_id": doc_id, "page": page, "text": f"{doc_id} page {page} {fmt(value)} {unit} {period} {quote}"}
            out.append(f"[{sid}] {comp} {metric} {period}: {fmt(value)} {unit}"
                       f"{f' ({basis})' if basis else ''} — {doc_id} p{page}: \"{quote}\"")
        return "\n".join(out) or "No facts found."

    def _calculator(self, expression, sources):
        try:
            result = calculate(expression)
        except (ValueError, SyntaxError, ZeroDivisionError) as e:
            return f"Error: {e}", True
        sid = f"C{sum(s.startswith('C') for s in sources) + 1}"
        sources[sid] = {"doc_id": None, "page": None, "text": f"{expression} = {fmt(round(result, 4))}"}
        return f"[{sid}] {expression} = {fmt(round(result, 4))}", False

    def run_tool(self, name: str, args: dict, sources: dict) -> tuple[str, bool]:
        if name == "search_docs":
            return self._search_docs(args["query"], args["company"], args["year"], sources), False
        if name == "query_facts":
            return self._query_facts(args["company"], args["metric"], args["year"], sources), False
        if name == "calculator":
            return self._calculator(args["expression"], sources)
        return f"Error: unknown tool {name}", True

    # -- loop ----------------------------------------------------------------
    def answer(self, question: str) -> dict:
        messages = [{"role": "user", "content": question}]
        sources: dict[str, dict] = {}
        record = {"question": question, "steps": [], "calls": []}
        final_text = None
        for step in range(MAX_STEPS):
            start = time.perf_counter()
            resp = self.client.beta.messages.create(
                model=MODEL, max_tokens=8000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            )
            record["calls"].append({"step": step + 1, "model": resp.model, "stop_reason": resp.stop_reason,
                                    "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                                    "latency_s": round(time.perf_counter() - start, 2),
                                    "cost_usd": round(cost(resp.model, resp.usage), 6)})
            if resp.stop_reason == "refusal":
                break
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                final_text = next((b.text for b in resp.content if b.type == "text"), "")
                break
            messages.append({"role": "assistant", "content": resp.content})  # keep thinking + tool_use blocks
            results = []
            for tu in tool_uses:
                output, is_error = self.run_tool(tu.name, tu.input, sources)
                record["steps"].append({"tool": tu.name, "input": tu.input, "error": is_error})
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": output, "is_error": is_error})
            messages.append({"role": "user", "content": results})  # all results in one message

        totals = {"cost_usd": round(sum(c["cost_usd"] for c in record["calls"]), 6),
                  "latency_s": round(sum(c["latency_s"] for c in record["calls"]), 2),
                  "retrieved": sorted({f"{s['doc_id']} p{s['page']}" for s in sources.values() if s["doc_id"]})}
        reason = ("refused" if record["calls"] and record["calls"][-1]["stop_reason"] == "refusal"
                  else "step limit reached" if final_text is None else None)
        parsed = None
        if not reason:
            try:
                parsed = AgentAnswer.model_validate_json(final_text)
            except ValidationError as e:
                reason = f"invalid final answer: {e.errors()[0]['msg']}"
        if parsed is None:
            return {**record, **totals, "status": "error", "answer": "", "citations": [], "problems": [reason]}

        citations, problems, cited_text = [], [], ""
        for c in parsed.citations:
            src = sources.get(c.source_id)
            if src is None:
                problems.append(f"cites {c.source_id}, which no tool returned")
                continue
            cited_text += " " + src["text"]
            if re.sub(r"\s+", " ", c.quote).strip() not in re.sub(r"\s+", " ", src["text"]):
                problems.append(f"quote not found in {c.source_id}: {c.quote[:60]!r}")
            if src["doc_id"]:
                citations.append({"source_id": c.source_id, "doc_id": src["doc_id"], "page": src["page"],
                                  "quote": c.quote})
        if parsed.status == "answered":
            if not citations:
                problems.append("answered without citing a document")
            missing = ungrounded_numbers(parsed.answer, cited_text)
            if missing:
                problems.append(f"numbers not in cited sources: {sorted(missing)}")
        return {**record, **totals, "status": parsed.status, "answer": parsed.answer,
                "citations": citations, "problems": problems}


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "How much did Google's data centre electricity use grow from 2024 to 2025?"
    r = Agent().answer(q)
    print(f"Q: {q}\n")
    for s in r["steps"]:
        print(f"  tool: {s['tool']}({json.dumps(s['input'])})")
    print(f"\n[{r['status']}] {r['answer']}\n")
    for c in r["citations"]:
        print(f"  - {c['doc_id']} p{c['page']}: \"{c['quote']}\"")
    if r["problems"]:
        print("\nProblems:", *r["problems"], sep="\n  - ")
    print(f"\n{len(r['calls'])} model calls, ${r['cost_usd']:.4f}, {r['latency_s']}s")
