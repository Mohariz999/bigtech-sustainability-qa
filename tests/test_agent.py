"""Tests for agent.py with a fake Claude client that scripts tool calls (no API key, no cost).

Run: .venv/bin/python -m pytest -q
"""
import json
import pathlib
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from agent import MAX_STEPS, Agent, calculate, period_matches  # noqa: E402
from extract import FACTS_SCHEMA  # noqa: E402
from test_answer import FakeClient, FakeSearcher, response  # noqa: E402


def tool_call(name, args, call_id="t1"):
    usage = SimpleNamespace(input_tokens=500, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=0)
    return SimpleNamespace(model="claude-opus-5-5", stop_reason="tool_use", usage=usage,
                           content=[SimpleNamespace(type="tool_use", id=call_id, name=name, input=args)])


def final(answer, citations, status="answered"):
    return response(json.dumps({"status": status, "answer": answer, "citations": citations}))


def make_agent(client):
    agent = Agent(searcher=FakeSearcher(), client=client, db=sqlite3.connect(":memory:"))
    agent.db.executescript("CREATE TABLE documents (doc_id TEXT);" + FACTS_SCHEMA)
    agent.db.executemany("INSERT INTO documents VALUES (?)", [("google_env_2026",), ("google_env_2025",)])
    agent.db.executemany(
        "INSERT INTO facts (fact_id, company, doc_id, page, metric, value, unit, period, basis, quote) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(1, "Google", "google_env_2026", 93, "datacenter_electricity", 30637100, "MWh", "2024", "", "Data centers MWh 30,637,100"),
         (2, "Google", "google_env_2026", 93, "datacenter_electricity", 42415800, "MWh", "2025", "", "Data centers MWh 42,415,800")])
    return agent


def test_calculator_does_arithmetic_but_runs_no_code():
    assert calculate("(42415800-30637100)/30637100*100") == pytest.approx(38.445, rel=1e-4)
    assert calculate("1,000 + 1") == 1001
    with pytest.raises(ValueError):
        calculate("__import__('os').system('echo hi')")


@pytest.mark.parametrize("expression, expected", [
    ("2 + 3 * 4", 14), ("(2 + 3) * 4", 20), ("-5 + +2", -3), ("7 / 2", 3.5), ("1.05 ** 10", 1.05 ** 10),
    ("2 ** -2", 0.25), ("14,473,100 / 12,233,300 * 100 - 100", 14473100 / 12233300 * 100 - 100),
    ("1e15 * 1e15", 1e30), (" + ".join(["1"] * 99), 99),
])
def test_calculator_normal_arithmetic(expression, expected):
    assert calculate(expression) == pytest.approx(expected)


@pytest.mark.parametrize("expression", ["", "1 +", "abs(-1)", "x + 1", "1 if 1 else 2", "[1, 2]", "'a' * 3",
                                        "True + 1", "1 // 2", "5 % 2", "1 < 2", "(-8) ** 0.5", "1 + 2j"])
def test_calculator_rejects_invalid_expressions(expression):
    with pytest.raises((ValueError, SyntaxError)):
        calculate(expression)


@pytest.mark.parametrize("expression, message", [
    ("1" * 501, "longer than"),                             # expression length
    ("+".join(["1"] * 150), "too long or too deeply"),     # node count
    ("-" * 101 + "1", "too long or too deeply"),           # nesting depth (used to raise RecursionError at ~1000)
    ("1" + "0" * 31, "larger than"),                       # operand magnitude
    ("9 ** 101", "exponents above"),                       # exponent
    ("(((10**100)**100)**100)**100", "larger than"),       # used to hang for over 30 seconds
    ("((10**100)**100)**10", "larger than"),               # used to raise an uncaught OverflowError
    ("1e20 * 1e20", "larger than"),                        # intermediate result
    ("10.0 ** 99 * 10.0 ** 99 * 10.0 ** 99 * 10.0 ** 99", "larger than"),
    ("1e308 * 10", "larger than"),                         # used to return inf
    ("1e308 * 10 - 1e308 * 10", "larger than"),            # used to return nan
])
def test_calculator_refuses_resource_exhaustion_quickly(expression, message):
    start = time.perf_counter()
    with pytest.raises(ValueError, match=message):
        calculate(expression)
    assert time.perf_counter() - start < 0.5


def test_calculator_errors_reach_the_model_as_tool_errors_not_crashes():
    agent = make_agent(FakeClient())
    for expression in ["-" * 5000 + "1", "((10**100)**100)**10", "1e308 * 10"]:
        output, is_error = agent._calculator(expression, {})
        assert is_error and output.startswith("Error:")


def test_facts_then_calculator_then_grounded_answer():
    client = FakeClient(
        tool_call("query_facts", {"company": "Google", "metric": "datacenter_electricity", "year": "any"}),
        tool_call("calculator", {"expression": "(42415800-30637100)/30637100*100"}),
        final("Google's data centre electricity grew about 38.4%, from 30,637,100 MWh in 2024 to 42,415,800 MWh in 2025.",
              [{"source_id": "F1", "quote": "Data centers MWh 30,637,100"},
               {"source_id": "F2", "quote": "Data centers MWh 42,415,800"},
               {"source_id": "C1", "quote": "38.4"}]),
    )
    r = make_agent(client).answer("How much did Google's data centre electricity grow from 2024 to 2025?")
    assert [s["tool"] for s in r["steps"]] == ["query_facts", "calculator"]
    assert r["status"] == "answered" and r["problems"] == []
    assert {(c["doc_id"], c["page"]) for c in r["citations"]} == {("google_env_2026", 93)}
    # tool results go back in one user message, matched to the tool_use id
    assert client.requests[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"


def test_year_filter_on_facts():
    agent = make_agent(FakeClient())
    out = agent._query_facts("Google", "datacenter_electricity", "2025", {})
    assert "42,415,800" in out and "30,637,100" not in out


def test_year_filter_runs_before_the_row_limit():
    # 45 Amazon rows (none for 2025) sort before Meta's, so filtering after LIMIT 40 used to drop Meta's 2025 fact.
    agent = make_agent(FakeClient())
    agent.db.executemany(
        "INSERT INTO facts (company, doc_id, page, metric, value, unit, period, basis, quote) VALUES (?,?,?,?,?,?,?,?,?)",
        [("Amazon", "amazon_sr_2024", 1, "total_emissions", 60 + i, "MMT", str(1980 + i), "", "x") for i in range(45)]
        + [("Meta", "meta_sr_2026", 7, "total_emissions", 8.4, "MMT", "2025", "", "8.4 million")])
    sources = {}
    out = agent._query_facts("any", "total_emissions", "2025", sources)
    assert "Meta total_emissions 2025: 8.4" in out and len(sources) == 1


@pytest.mark.parametrize("period, year, expected", [
    ("2025", "2025", True), ("FY25", "2025", True), ("FY 2025", "2025", True), ("fy25", "2025", True),
    ("FY2025", "2025", True), ("January 2025", "2025", True), ("2010-2025", "2025", True),
    ("2024", "2025", False), ("FY24", "2025", False), ("FY251", "2025", False), ("FY 2024", "2025", False),
])
def test_period_matching_keeps_its_rules(period, year, expected):
    # reporting-year semantics: matches the period the fact describes, never the report it came from
    assert period_matches(period, year) is expected


def test_year_filter_uses_the_fact_period_not_the_report():
    agent = make_agent(FakeClient())
    # a 2025 figure published in the 2026 report, and a 2024 figure restated in that same report
    out = agent._query_facts("Google", "datacenter_electricity", "2024", {})
    assert "30,637,100" in out and "42,415,800" not in out


def test_citing_a_source_no_tool_returned_is_flagged():
    client = FakeClient(final("Google's PUE was 1.09.", [{"source_id": "F99", "quote": "1.09"}]))
    r = make_agent(client).answer("What was Google's PUE?")
    assert any("no tool returned" in p for p in r["problems"])


def test_number_not_in_any_source_is_flagged():
    client = FakeClient(
        tool_call("query_facts", {"company": "Google", "metric": "datacenter_electricity", "year": "2025"}),
        final("Google used 50,000,000 MWh in 2025.", [{"source_id": "F2", "quote": "Data centers MWh 42,415,800"}]),
    )
    r = make_agent(client).answer("How much electricity did Google's data centres use in 2025?")
    assert any("50000000" in p for p in r["problems"])


def test_step_limit_stops_a_looping_agent():
    calls = [tool_call("calculator", {"expression": "1+1"}, call_id=f"t{i}") for i in range(MAX_STEPS)]
    r = make_agent(FakeClient(*calls)).answer("loop forever")
    assert r["status"] == "error" and r["problems"] == ["step limit reached"] and len(r["calls"]) == MAX_STEPS


def test_bad_calculator_input_is_returned_as_tool_error():
    client = FakeClient(tool_call("calculator", {"expression": "import os"}),
                        final("", [], status="not_found"))
    r = make_agent(client).answer("x")
    assert r["steps"][0]["error"] is True and client.requests[1]["messages"][-1]["content"][0]["is_error"] is True
