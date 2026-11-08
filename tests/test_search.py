"""Tests for keyword search in search.py on a small in-memory corpus (no embedding model, no data/ files).

Run: .venv/bin/python -m pytest -q
"""
import pathlib
import sqlite3
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from ingest import SCHEMA  # noqa: E402
from search import Searcher  # noqa: E402

DOCS = [("google_env_2026", "Google", 2026, "2025"), ("google_env_2025", "Google", 2025, "2024"),
        ("meta_sr_2025", "Meta", 2025, "2024"), ("microsoft_esr_2026", "Microsoft", 2026, "FY2025")]


@pytest.fixture
def searcher():
    """600 Google chunks that match 'water' strongly, then a few weaker matches from other reports.
    Before the fix, the 500 best matches overall were taken first and filtered after, so the
    Meta and Microsoft chunks never made it into a filtered result."""
    db = sqlite3.connect(":memory:")
    db.executescript(SCHEMA)
    db.executemany("INSERT INTO documents VALUES (?, ?, ?, ?, ?, 10)",
                   [(d, f"{c} report", c, ry, dy) for d, c, ry, dy in DOCS])
    chunks = [("google_env_2026", f"water water water consumption {i}") for i in range(600)]
    chunks += [("google_env_2025", "water use in 2024 rose"),
               ("meta_sr_2025", "Meta water withdrawal and other figures for the year"),
               ("meta_sr_2025", "Meta energy use with no matching term"),
               ("microsoft_esr_2026", "Microsoft water replenishment in fiscal year 2025")]
    db.executemany("INSERT INTO chunks (doc_id, page, chunk_idx, text, n_words) VALUES (?, 1, 0, ?, 5)", chunks)
    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    s = Searcher.__new__(Searcher)  # skip __init__: it loads embeddings and the model, which keyword() doesn't use
    s.db = db
    return s


def test_company_filter_applies_before_the_result_limit(searcher):
    hits = searcher.keyword("water", 5, company="Meta")
    assert [h.doc_id for h in hits] == ["meta_sr_2025"] and "withdrawal" in hits[0].text


def test_year_filter_applies_before_the_result_limit(searcher):
    # '2025' also matches Microsoft's 'FY2025'; Google's 2026 report describes 2025 too
    hits = searcher.keyword("water", 700, data_year="2025")
    assert {h.doc_id for h in hits} == {"google_env_2026", "microsoft_esr_2026"} and len(hits) == 601
    assert [h.doc_id for h in searcher.keyword("water", 5, data_year="2024", company="Google")] == ["google_env_2025"]


def test_company_and_year_together(searcher):
    assert [h.doc_id for h in searcher.keyword("water", 5, company="Microsoft", data_year="2025")] == ["microsoft_esr_2026"]
    assert searcher.keyword("water", 5, company="Microsoft", data_year="2024") == []


def test_unfiltered_ranking_is_bm25_best_first(searcher):
    hits = searcher.keyword("water", 3)
    assert len(hits) == 3 and all(h.doc_id == "google_env_2026" for h in hits)
    assert hits[0].score >= hits[1].score >= hits[2].score > 0  # bm25 is negated: higher = better


def test_filter_matches_allowed_ids_used_by_vector_search(searcher):
    # keyword() and vector() must agree on which chunks a filter allows
    for company, year in [("Meta", None), (None, "2025"), ("Google", "2024")]:
        allowed = searcher._allowed_ids(company, year)
        assert {h.chunk_id for h in searcher.keyword("water", 1000, company, year)} <= allowed


def test_no_search_terms_returns_nothing(searcher):
    assert searcher.keyword("what was the", 5) == []
