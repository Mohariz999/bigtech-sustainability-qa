"""Turn the PDFs in docs/ into searchable chunks in data/corpus.sqlite.

Steps, per document:
1. Extract text page by page (PyMuPDF).
2. Drop boilerplate: short lines near the top or bottom of a page that
   repeat on many pages (running headers, navigation bars, footers).
3. Split each page into overlapping word windows (chunks). Chunks never
   cross a page, so every chunk has exactly one page to cite.
4. Store documents + chunks in SQLite, with an FTS5 index for keyword
   search and an embedding per chunk for meaning-based search.

Run: python ingest.py
"""
import collections
import csv
import pathlib
import re
import sqlite3

import numpy as np
import pymupdf
from fastembed import TextEmbedding

ROOT = pathlib.Path(__file__).parent
DB_PATH = ROOT / "data" / "corpus.sqlite"
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

CHUNK_WORDS = 200        # words per chunk
OVERLAP_WORDS = 50       # words shared with the previous chunk
BOILERPLATE_SHARE = 0.3  # a line on more than 30% of a doc's pages is boilerplate
EDGE_LINES = 8           # header/footer zone: first and last 8 lines of a page
MIN_PAGE_WORDS = 20      # skip near-empty pages (covers, full-page photos)

SCHEMA = """
CREATE TABLE documents (
    doc_id      TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    report_year INTEGER NOT NULL,   -- year published
    data_year   TEXT NOT NULL,      -- year the numbers describe
    n_pages     INTEGER NOT NULL
);
CREATE TABLE chunks (
    chunk_id   INTEGER PRIMARY KEY,
    doc_id     TEXT NOT NULL REFERENCES documents(doc_id),
    page       INTEGER NOT NULL,    -- 1-based PDF page index
    chunk_idx  INTEGER NOT NULL,    -- position within the page
    text       TEXT NOT NULL,
    n_words    INTEGER NOT NULL,
    embedding  BLOB                 -- float32 vector, normalised
);
CREATE INDEX chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE chunks_fts USING fts5(text, content='chunks', content_rowid='chunk_id');
"""


def line_key(line: str) -> str:
    """Normalise a line so 'Page 12' and 'Page 13' count as the same boilerplate."""
    return re.sub(r"\d+", "#", line.strip().lower())


def is_candidate(line: str) -> bool:
    """Only short lines with at most one number can be boilerplate.
    (A first version had no such rule and deleted whole rows of table numbers,
    because '456,119 429,405' and '17,429,800 20,616,500' normalise to the same key.)"""
    return len(line.split()) <= 12 and len(re.findall(r"\d+", line)) <= 1


def clean_pages(doc: pymupdf.Document) -> list[str]:
    pages = [[l for l in p.get_text().replace("’", "'").splitlines() if l.strip()] for p in doc]
    # Headers and footers live at the edges of a page: only look at the first and last few lines.
    edges = [{i for i in range(len(lines)) if i < EDGE_LINES or i >= len(lines) - EDGE_LINES} for lines in pages]
    counts = collections.Counter(
        k for lines, idx in zip(pages, edges) for k in {line_key(lines[i]) for i in idx if is_candidate(lines[i])})
    limit = max(3, BOILERPLATE_SHARE * len(pages))
    boilerplate = {k for k, n in counts.items() if n > limit}
    return [re.sub(r"\s+", " ", " ".join(
                l for i, l in enumerate(lines) if not (i in idx and line_key(l) in boilerplate))).strip()
            for lines, idx in zip(pages, edges)]


def chunk_page(text: str) -> list[str]:
    words = text.split()
    if len(words) < MIN_PAGE_WORDS:
        return []
    step = CHUNK_WORDS - OVERLAP_WORDS
    return [" ".join(words[i:i + CHUNK_WORDS]) for i in range(0, max(1, len(words) - OVERLAP_WORDS), step)]


def main() -> None:
    DB_PATH.parent.mkdir(exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)

    manifest = list(csv.DictReader(open(ROOT / "docs" / "manifest.csv")))
    for m in manifest:
        doc = pymupdf.open(ROOT / "docs" / m["file"])
        pages = clean_pages(doc)
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?)",
                   (m["doc_id"], m["title"], m["company"], int(m["report_year"]), m["data_year"], len(pages)))
        n = 0
        for page_no, text in enumerate(pages, start=1):
            for idx, chunk in enumerate(chunk_page(text)):
                db.execute("INSERT INTO chunks (doc_id, page, chunk_idx, text, n_words) VALUES (?,?,?,?,?)",
                           (m["doc_id"], page_no, idx, chunk, len(chunk.split())))
                n += 1
        print(f"{m['doc_id']:26} {len(pages):4} pages -> {n:4} chunks")

    db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")

    rows = db.execute("SELECT chunk_id, text FROM chunks ORDER BY chunk_id").fetchall()
    print(f"Embedding {len(rows)} chunks with {EMBED_MODEL} ...")
    model = TextEmbedding(EMBED_MODEL)
    for (chunk_id, _), vec in zip(rows, model.embed([t for _, t in rows], batch_size=64)):
        vec = np.asarray(vec, dtype=np.float32)
        db.execute("UPDATE chunks SET embedding=? WHERE chunk_id=?", ((vec / np.linalg.norm(vec)).tobytes(), chunk_id))
    db.commit()
    print(f"Wrote {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
