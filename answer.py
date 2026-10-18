"""Answer a question from the reports, with citations, or say NOT_FOUND.

Pipeline:
1. Retrieve the top passages with hybrid search (company/year filters from the question).
2. Send them to Claude with strict rules: use only these passages, cite passage
   numbers, quote the supporting text, or return not_found.
3. Force the reply into a JSON schema, then validate it with Pydantic.
   Invalid output -> retry once with the error message; still invalid -> error.
4. Check the answer is grounded: every quote must appear in its cited passage,
   and every number in the answer must appear in the cited passages.
5. Log tokens, cost and latency for every call.

The model only cites passage *numbers*; this code maps them to (doc_id, page),
so the model can't invent a document or page.

Needs ANTHROPIC_API_KEY in the environment or in a .env file (never committed).
Try it: python answer.py "What was Google's PUE in 2025?"
"""
import json
import os
import pathlib
import re
import sys
import time
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError

from search import Searcher, company_in, year_in

ROOT = pathlib.Path(__file__).parent
MODEL = "claude-opus-5-5"
EFFORT = "medium"
TOP_K = 5
# $ per million tokens (input, output), standard API rates. The fallback models are listed
# so a request that falls back is still priced correctly.
PRICES = {"claude-opus-5-5": (4.00, 20.00), "claude-opus-5": (5.00, 25.00), "claude-opus-4-8": (5.00, 25.00)}

SYSTEM = """You answer questions about company sustainability reports using ONLY the numbered passages provided.

Rules:
- Use only facts stated in the passages. Never use outside knowledge, even if you know the answer.
- If the passages do not answer the question, set status to "not_found". A passage about a similar
  metric for a different company or a different year is NOT an answer.
- Cite the passage numbers you used. For each, copy a short exact quote (under 30 words) that supports the answer.
- If the question does not name a year and passages from different report years disagree, give the most
  recent figure and say which report year it comes from.
- Keep the answer to one to three sentences, including the numbers and units as written in the passages."""


class Citation(BaseModel):
    passage: int = Field(description="Number of the passage, as labelled [n]")
    quote: str = Field(description="Exact short quote from that passage supporting the answer")


class ModelAnswer(BaseModel):
    status: Literal["answered", "not_found"]
    answer: str
    citations: list[Citation]


# JSON schema sent to the API (structured outputs need additionalProperties: false everywhere).
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["answered", "not_found"]},
        "answer": {"type": "string"},
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"passage": {"type": "integer"}, "quote": {"type": "string"}},
                "required": ["passage", "quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["status", "answer", "citations"],
    "additionalProperties": False,
}


def load_dotenv(path: pathlib.Path = ROOT / ".env") -> None:
    """Minimal .env loader so the key never has to be typed into code."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def format_passages(hits) -> str:
    return "\n\n".join(f"[{i}] (source: {h.doc_id}, page {h.page})\n{h.text}" for i, h in enumerate(hits, start=1))


def cost(model: str, usage) -> float:
    """input_tokens excludes cached tokens: cache writes bill at 1.25x input, cache reads at 0.1x."""
    inp, out = PRICES.get(model, PRICES[MODEL])
    written = getattr(usage, "cache_creation_input_tokens", 0) or 0
    read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (usage.input_tokens * inp + written * inp * 1.25 + read * inp * 0.1 + usage.output_tokens * out) / 1e6


def numbers_in(text: str) -> set[str]:
    """Numbers as written, without thousands separators: '42,415,800' -> '42415800'."""
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def ungrounded_numbers(answer: str, source_text: str) -> set[str]:
    """Numbers in the answer that don't appear in the sources, even after rounding.
    '38.4' is grounded by a source '38.4446'; '38' is grounded by '38.4446'; '42.4' is not grounded by '42415800'."""
    sources = [float(n) for n in numbers_in(source_text)]
    missing = set()
    for n in numbers_in(answer):
        decimals = len(n.split(".")[1]) if "." in n else 0
        if not any(round(s, decimals) == float(n) for s in sources):
            missing.add(n)
    return missing


def grounding_problems(parsed: ModelAnswer, hits) -> list[str]:
    problems = []
    cited_text = ""
    for c in parsed.citations:
        if not 1 <= c.passage <= len(hits):
            problems.append(f"cites passage [{c.passage}], which doesn't exist")
            continue
        passage = hits[c.passage - 1].text
        # The source label is shown to the model too, so "(2026 report)" or "page 5" counts as grounded.
        cited_text += f" {hits[c.passage - 1].doc_id} page {hits[c.passage - 1].page} {passage}"
        if re.sub(r"\s+", " ", c.quote.replace("’", "'")).strip() not in passage:
            problems.append(f"quote not found in passage [{c.passage}]: {c.quote[:60]!r}")
    if parsed.status == "answered":
        if not parsed.citations:
            problems.append("answered without any citation")
        missing = ungrounded_numbers(parsed.answer, cited_text)
        if missing:
            problems.append(f"numbers not in cited passages: {sorted(missing)}")
    return problems


class Answerer:
    def __init__(self, searcher: Searcher | None = None, client: anthropic.Anthropic | None = None):
        load_dotenv()
        self.searcher = searcher or Searcher()
        # timeout per attempt; the SDK retries 429/5xx/connection errors with backoff.
        self.client = client or anthropic.Anthropic(timeout=90.0, max_retries=3)

    def _call(self, messages: list[dict]):
        return self.client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=SYSTEM,  # too short to be worth prompt caching (below the minimum cacheable size)
            messages=messages,
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": ANSWER_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if Claude declines, the API re-runs the request on a fallback model
        )

    def answer(self, question: str) -> dict:
        company, year = company_in(question), year_in(question)
        hits = self.searcher.hybrid(question, TOP_K, company=company, data_year=year)
        record = {"question": question, "filters": {"company": company, "data_year": year},
                  "retrieved": [f"{h.doc_id} p{h.page}" for h in hits], "calls": []}
        if not hits:  # nothing passed the filters: don't pay for an LLM call
            return {**record, "status": "not_found", "answer": "", "citations": [], "problems": [],
                    "cost_usd": 0.0, "latency_s": 0.0}

        messages = [{"role": "user", "content": f"Passages:\n\n{format_passages(hits)}\n\nQuestion: {question}"}]
        parsed, error = None, None
        for attempt in range(2):  # one retry if the output fails validation
            start = time.perf_counter()
            resp = self._call(messages)
            latency = time.perf_counter() - start
            record["calls"].append({
                "attempt": attempt + 1, "model": resp.model, "stop_reason": resp.stop_reason,
                "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens,
                "cache_read_tokens": getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
                "latency_s": round(latency, 2), "cost_usd": round(cost(resp.model, resp.usage), 6)})
            if resp.stop_reason == "refusal":
                error = "refused"
                break
            text = next((b.text for b in resp.content if b.type == "text"), "")
            try:
                parsed = ModelAnswer.model_validate_json(text)
                break
            except ValidationError as e:
                error = f"invalid output: {e.errors()[0]['msg']}"
                messages += [{"role": "assistant", "content": text},
                             {"role": "user", "content": f"Your reply did not match the schema ({error}). Reply again with valid JSON only."}]

        totals = {"cost_usd": round(sum(c["cost_usd"] for c in record["calls"]), 6),
                  "latency_s": round(sum(c["latency_s"] for c in record["calls"]), 2)}
        if parsed is None:
            return {**record, **totals, "status": "error", "answer": "", "citations": [], "problems": [error]}
        citations = [{"passage": c.passage, "doc_id": hits[c.passage - 1].doc_id, "page": hits[c.passage - 1].page,
                      "quote": c.quote} for c in parsed.citations if 1 <= c.passage <= len(hits)]
        return {**record, **totals, "status": parsed.status, "answer": parsed.answer,
                "citations": citations, "problems": grounding_problems(parsed, hits)}


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or "What was Google's PUE in 2025?"
    result = Answerer().answer(q)
    print(f"Q: {q}\n\n[{result['status']}] {result['answer']}\n")
    for c in result["citations"]:
        print(f"  - {c['doc_id']} p{c['page']}: \"{c['quote']}\"")
    if result["problems"]:
        print("\nGrounding problems:", *result["problems"], sep="\n  - ")
    print(f"\ncost ${result['cost_usd']:.4f}, {result['latency_s']}s, calls: {len(result['calls'])}")
