# bigtech-sustainability-qa

Question answering and structured extraction over Big Tech sustainability reports (Google, Microsoft, Meta, Amazon, Apple, two years each where available). Every answer cites a report and page, the system says "not found" instead of guessing, and a 30-question evaluation set measures how often it is right.

**Status:** work in progress. The document set and evaluation set are done; the pipeline is next. See [NOTES.md](NOTES.md).

## Get the documents

```bash
python fetch_docs.py
```

This downloads 10 public reports into `docs/` and writes `docs/manifest.csv`. The PDFs are not committed to this repo.

## Check the eval set

```bash
pip install pymupdf
python eval/check_questions.py
```

This confirms each question's evidence quote is on its cited page and that each "not found" answer really is absent.
