"""Tests for answer.py using a fake Claude client and a fake searcher (no API key, no cost).

Run: .venv/bin/python -m pytest -q
"""
import json
import pathlib
import sys
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from answer import Answerer, cost  # noqa: E402
from search import Hit  # noqa: E402

PASSAGES = [
    Hit(1, "google_env_2026", 5, "Google's data centers operated with a 2025 fleet-wide average power usage "
        "effectiveness (PUE) of 1.09 and using 83% less overhead energy than the industry average.", 0.9),
    Hit(2, "amazon_sr_2025", 10, "In 2025, Amazon's data centers achieved a global PUE of 1.14.", 0.8),
]


class FakeSearcher:
    def __init__(self, hits=PASSAGES):
        self.hits = hits

    def hybrid(self, question, k, company=None, data_year=None):
        return self.hits[:k]


def response(text="", stop_reason="end_turn", model="claude-opus-5-5"):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=100,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(model=model, stop_reason=stop_reason, usage=usage,
                           content=[SimpleNamespace(type="text", text=text)])


class FakeClient:
    """Returns the queued responses in order and records each request."""
    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def reply(status="answered", answer="Google's 2025 PUE was 1.09.", citations=None):
    if citations is None:
        citations = [{"passage": 1, "quote": "2025 fleet-wide average power usage effectiveness (PUE) of 1.09"}]
    return json.dumps({"status": status, "answer": answer, "citations": citations})


def run(client, hits=PASSAGES, question="What was Google's PUE in 2025?"):
    return Answerer(searcher=FakeSearcher(hits), client=client).answer(question)


def test_grounded_answer_maps_citation_to_doc_and_page():
    r = run(FakeClient(response(reply())))
    assert r["status"] == "answered"
    assert r["citations"][0]["doc_id"] == "google_env_2026" and r["citations"][0]["page"] == 5
    assert r["problems"] == []


def test_made_up_number_is_flagged():
    r = run(FakeClient(response(reply(answer="Google's 2025 PUE was 1.07."))))
    assert any("1.07" in p for p in r["problems"])


def test_quote_not_in_passage_is_flagged():
    bad = [{"passage": 1, "quote": "Google achieved a perfect PUE"}]
    r = run(FakeClient(response(reply(citations=bad))))
    assert any("quote not found" in p for p in r["problems"])


def test_citing_a_passage_that_does_not_exist_is_flagged():
    r = run(FakeClient(response(reply(citations=[{"passage": 7, "quote": "x"}]))))
    assert any("doesn't exist" in p for p in r["problems"])
    assert r["citations"] == []


def test_invalid_json_is_retried_once():
    client = FakeClient(response('{"status": "maybe"}'), response(reply()))
    r = run(client)
    assert r["status"] == "answered" and len(r["calls"]) == 2
    assert "did not match the schema" in client.requests[1]["messages"][-1]["content"]


def test_invalid_twice_is_an_error_not_a_guess():
    r = run(FakeClient(response("not json"), response("still not json")))
    assert r["status"] == "error" and r["answer"] == ""


def test_not_found_is_passed_through():
    r = run(FakeClient(response(reply(status="not_found", answer="", citations=[]))),
            question="What was Meta's PUE in 2024?")
    assert r["status"] == "not_found" and r["problems"] == []


def test_refusal_is_reported():
    r = run(FakeClient(response(stop_reason="refusal")))
    assert r["status"] == "error" and r["problems"] == ["refused"]


def test_no_passages_means_no_llm_call():
    client = FakeClient()
    r = run(client, hits=[])
    assert r["status"] == "not_found" and client.requests == [] and r["cost_usd"] == 0


def test_request_uses_schema_and_fallback():
    client = FakeClient(response(reply()))
    run(client)
    req = client.requests[0]
    assert req["output_config"]["format"]["type"] == "json_schema"
    assert req["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in req["betas"]


def test_cost_counts_cache_writes_and_reads():
    usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000,
                            cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    # 4 (input) + 5 (cache write, 1.25x) + 0.4 (cache read, 0.1x) + 20 (output)
    assert abs(cost("claude-opus-5-5", usage) - 29.4) < 1e-9


# -- scoring rules in eval/run_answers.py -----------------------------------
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "eval"))
from run_answers import cites_gold, is_correct  # noqa: E402


def test_scoring_ignores_case_and_thousands_separators():
    q = {"category": "table", "must_contain_any": ["42,415,800", "42.4"]}
    assert is_correct(q, {"status": "answered", "answer": "42415800 MWh in 2025"})
    assert not is_correct(q, {"status": "answered", "answer": "30,637,100 MWh"})


def test_scoring_must_contain_needs_every_value():
    q = {"category": "comparison", "must_contain": ["1.09", "1.14"]}
    assert is_correct(q, {"status": "answered", "answer": "Google 1.09 vs Amazon 1.14"})
    assert not is_correct(q, {"status": "answered", "answer": "Google 1.09"})


def test_not_found_question_is_only_correct_when_not_found():
    q = {"category": "not_found_distractor"}
    assert is_correct(q, {"status": "not_found", "answer": ""})
    assert not is_correct(q, {"status": "answered", "answer": "36 data centres"})


def test_citation_scoring_uses_gold_pages():
    q = {"sources": [{"doc_id": "google_env_2026", "pages": [5, 15]}]}
    assert cites_gold(q, {"citations": [{"doc_id": "google_env_2026", "page": 15}]})
    assert not cites_gold(q, {"citations": [{"doc_id": "google_env_2025", "page": 5}]})
