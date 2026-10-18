"""Search the chunks in data/corpus.sqlite.

Three methods, all with the same optional metadata filter (company, data_year)
applied in SQL *before* ranking:
- keyword: SQLite FTS5 with BM25 ranking. Good at exact terms ("PUE", "Scope 2").
- vector:  cosine similarity between embeddings. Good at meaning ("water efficiency" ~ "WUE").
- hybrid:  both lists merged with Reciprocal Rank Fusion (RRF).

Try it: python search.py "What was Google's PUE in 2025?"
"""
import pathlib
import re
import sqlite3
import sys
from dataclasses import dataclass

import numpy as np
from fastembed import TextEmbedding

ROOT = pathlib.Path(__file__).parent
DB_PATH = ROOT / "data" / "corpus.sqlite"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
COMPANIES = ["Google", "Microsoft", "Meta", "Amazon", "Apple"]
RRF_K = 60  # standard constant: dampens the weight of the very top ranks

# Common words that would make FTS5 match almost everything.
STOPWORDS = set("""a an and are as at be by did do does for from has have how in is it its of on or
the to was were what when where which who why with much many their they this that there""".split())


@dataclass
class Hit:
    chunk_id: int
    doc_id: str
    page: int
    text: str
    score: float


class Searcher:
    def __init__(self, db_path: pathlib.Path = DB_PATH):
        self.db = sqlite3.connect(db_path)
        rows = self.db.execute("SELECT chunk_id, embedding FROM chunks ORDER BY chunk_id").fetchall()
        self.ids = np.array([r[0] for r in rows])
        self.matrix = np.vstack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
        self.model = TextEmbedding(EMBED_MODEL)

    # -- filtering -----------------------------------------------------------
    def _allowed_ids(self, company: str | None, data_year: str | None) -> set[int] | None:
        if not company and not data_year:
            return None
        sql = "SELECT c.chunk_id FROM chunks c JOIN documents d USING (doc_id) WHERE 1=1"
        args: list = []
        if company:
            sql += " AND d.company = ?"
            args.append(company)
        if data_year:  # '2025' matches data_year '2025' and 'FY2025'
            sql += " AND d.data_year IN (?, ?)"
            args += [data_year, f"FY{data_year}"]
        return {r[0] for r in self.db.execute(sql, args)}

    def _hits(self, scored: list[tuple[int, float]]) -> list[Hit]:
        out = []
        for chunk_id, score in scored:
            doc_id, page, text = self.db.execute(
                "SELECT doc_id, page, text FROM chunks WHERE chunk_id=?", (int(chunk_id),)).fetchone()
            out.append(Hit(int(chunk_id), doc_id, page, text, float(score)))
        return out

    # -- methods -------------------------------------------------------------
    def keyword(self, query: str, k: int = 5, company=None, data_year=None) -> list[Hit]:
        terms = [t for t in re.findall(r"[A-Za-z0-9]+", query.lower()) if t not in STOPWORDS]
        if not terms:
            return []
        fts_query = " OR ".join(f'"{t}"' for t in terms)
        allowed = self._allowed_ids(company, data_year)
        rows = self.db.execute(
            "SELECT rowid, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY bm25(chunks_fts) LIMIT 500",
            (fts_query,)).fetchall()
        rows = [(cid, -s) for cid, s in rows if allowed is None or cid in allowed]  # bm25(): lower = better
        return self._hits(rows[:k])

    def vector(self, query: str, k: int = 5, company=None, data_year=None) -> list[Hit]:
        q = np.asarray(next(iter(self.model.query_embed([query]))), dtype=np.float32)
        sims = self.matrix @ (q / np.linalg.norm(q))
        allowed = self._allowed_ids(company, data_year)
        candidates = np.arange(len(self.ids))
        if allowed is not None:  # only rank chunks that pass the filter (may be none)
            candidates = candidates[np.isin(self.ids, list(allowed))]
        top = candidates[np.argsort(-sims[candidates])[:k]]
        return self._hits([(self.ids[i], sims[i]) for i in top])

    def hybrid(self, query: str, k: int = 5, company=None, data_year=None, pool: int = 30) -> list[Hit]:
        fused: dict[int, float] = {}
        for ranked in (self.keyword(query, pool, company, data_year), self.vector(query, pool, company, data_year)):
            for rank, h in enumerate(ranked):
                fused[h.chunk_id] = fused.get(h.chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
        top = sorted(fused.items(), key=lambda x: -x[1])[:k]
        return self._hits(top)


def company_in(question: str) -> str | None:
    """Simple query parsing: if exactly one company is named, filter to it."""
    named = [c for c in COMPANIES if re.search(rf"\b{c}", question, re.I)]
    return named[0] if len(named) == 1 else None


DATA_YEARS = {"2024", "2025"}  # years some report in docs/ has data for


def year_in(question: str) -> str | None:
    """If exactly one data year is named ('2025', 'FY25'), filter to reports whose data covers it.
    Years no report covers ('since 2021', 'by 2030', '2020 baseline') are ignored, and
    questions spanning two years ('from 2024 to 2025') get no year filter."""
    years = {y for y in re.findall(r"\b(20\d\d)\b", question)}
    years |= {f"20{y}" for y in re.findall(r"\bFY(\d\d)\b", question)}
    years &= DATA_YEARS
    return years.pop() if len(years) == 1 else None


if __name__ == "__main__":
    question = " ".join(sys.argv[1:]) or "What was Google's PUE in 2025?"
    s = Searcher()
    company, year = company_in(question), year_in(question)
    print(f"Q: {question}\nfilter: company={company} data_year={year}\n")
    for h in s.hybrid(question, 5, company=company, data_year=year):
        print(f"[{h.doc_id} p{h.page}] {h.text[:160]}...")
