"""Tests for extract.py and the extraction scorer, with a fake Claude client (no API key, no cost).

Run: .venv/bin/python -m pytest -q
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from extract import Extractor, Fact, check_fact  # noqa: E402
from run_extraction import year_of  # noqa: E402
from test_answer import FakeClient, response  # noqa: E402

PAGE = {"doc_id": "google_env_2026", "page": 93, "company": "Google",
        "text": "Electricity consumption Unit 2021 2022 2023 2024 2025 Data centers MWh 17,429,800 20,616,500 "
                "23,980,800 30,637,100 42,415,800 Offices and other facilities MWh 628,500"}
ROW = "Data centers MWh 17,429,800 20,616,500 23,980,800 30,637,100 42,415,800"


def fact(**kw):
    base = {"metric": "datacenter_electricity", "value": 42415800, "unit": "MWh", "period": "2025",
            "basis": "", "quote": ROW}
    return {**base, **kw}


def test_fact_with_quote_and_value_on_page_passes():
    assert check_fact(Fact(**fact()), PAGE["text"]) is None


def test_quote_not_on_page_is_rejected():
    assert check_fact(Fact(**fact(quote="Data centers used 42 TWh")), PAGE["text"]) == "quote not on page"


def test_converted_value_is_rejected():
    # 42.4 TWh is a conversion: the value isn't written in the quote, so it can't be verified.
    assert check_fact(Fact(**fact(value=42.4, unit="TWh")), PAGE["text"]) == "value not in quote"


def test_extract_page_adds_company_doc_and_page_and_splits_kept_rejected():
    reply = json.dumps({"facts": [fact(), fact(value=99, quote=ROW)]})
    log = Extractor(client=FakeClient(response(reply))).extract_page(PAGE)
    assert log["status"] == "ok" and len(log["kept"]) == 1 and len(log["rejected"]) == 1
    kept = log["kept"][0]
    assert (kept["company"], kept["doc_id"], kept["page"]) == ("Google", "google_env_2026", 93)
    assert log["rejected"][0]["reason"] == "value not in quote"


def test_unknown_metric_fails_validation_and_is_retried():
    bad = json.dumps({"facts": [fact(metric="happiness_index")]})
    good = json.dumps({"facts": [fact()]})
    client = FakeClient(response(bad), response(good))
    log = Extractor(client=client).extract_page(PAGE)
    assert log["status"] == "ok" and len(log["calls"]) == 2 and len(log["kept"]) == 1


def test_invalid_twice_stores_nothing():
    log = Extractor(client=FakeClient(response("nope"), response("still nope"))).extract_page(PAGE)
    assert log["status"].startswith("invalid") and log["kept"] == []


def test_year_of_handles_fiscal_years():
    assert year_of("2025") == 2025 and year_of("FY25") == 2025 and year_of("fiscal year 2024") == 2024
    assert year_of("since 2020 baseline") == 2020 and year_of("n/a") is None
