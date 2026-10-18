"""Check the eval set against the documents before using it to score anything.

For each question:
- answerable: its `evidence` quote must appear on one of the cited pages
  (1-based PDF page index, not the printed page number).
- not_found: none of its `absent_patterns` may match the documents in
  `absent_in` (or every document, if `absent_in` is missing).

Run: python eval/check_questions.py
"""
import json
import pathlib
import re
import sys

import pymupdf

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"


def normalise(text: str) -> str:
    text = text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    questions = [json.loads(line) for line in open(ROOT / "eval/questions.jsonl") if line.strip()]
    pages = {p.stem: [normalise(pg.get_text()) for pg in pymupdf.open(p)] for p in DOCS.glob("*.pdf")}

    failures = 0
    for q in questions:
        if q["category"].startswith("not_found"):
            scope = q.get("absent_in", list(pages))
            hits = [(d, i + 1, pat) for pat in q["absent_patterns"] for d in scope
                    for i, t in enumerate(pages[d]) if re.search(pat, t)]
            ok, detail = not hits, f"found in {hits[:3]}" if hits else f"absent from {len(scope)} doc(s)"
        else:
            evidence = normalise(q["evidence"])
            where = [(s["doc_id"], p) for s in q["sources"] for p in s["pages"]]
            found = [(d, p) for d, p in where if evidence in pages[d][p - 1]]
            ok, detail = bool(found), f"evidence on {found}" if found else f"evidence NOT on {where}"
        failures += not ok
        print(f"{'ok  ' if ok else 'FAIL'} {q['id']} [{q['category']}] {detail}")

    print(f"\n{len(questions) - failures}/{len(questions)} questions verified")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
