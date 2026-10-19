# Engineering log

What went wrong while building this, how I found out, and what I changed. Most of these were caught by a check or a test rather than by eye.

## Documents and ingest

**A sentence split by page-header debris.** Google's "we again matched 100% of our electricity consumption with renewable energy" starts on page 4 and ends on page 5, with the repeated navigation bar ("Introduction Impact stories Detailed disclosures…") in between. A chunker that splits by page cuts the sentence in half.

**The header cleanup deleted table data.** To remove repeated headers and footers, I dropped lines that appear on many pages, after replacing digits with `#` so "Page 12" and "Page 13" match. But table rows like "456,119 429,405 288,029" also normalise to the same pattern, so whole rows of Microsoft's emissions table disappeared.
- *Found by:* a check that each eval question's evidence quote still exists in the chunks after ingest.
- *Fix:* only remove short lines with at most one number, in the top or bottom 8 lines of a page.

## Search

**The answer key was too strict.** The same fact is often on several pages (highlights, detail, appendix), and six "misses" had found it on a different page. I read each page before adding it to the key. One I didn't add: an older Google report also says "PUE 1.09", but that's the 2024 figure. Same number, wrong source.

**The year filter made results worse at first.** "Since 2021", "by 2030" and "2020 baseline" are years too. Filtering on them matched no reports, and a bug made vector search return unfiltered junk instead of nothing.
- *Fix:* only filter on years some report has data for, and never return chunks outside the filter.

**Search scores can't detect "not found".** The best-match similarity for questions with no answer overlaps the range for answerable ones. The "not found" decision has to come from the model reading the passages.

## Answering

**A bad API key crashed the whole run.** A truncated key made the runner die with a traceback.
- *Fix:* stop with a clear message on a 401, and record other API errors per question instead of crashing.

**The grounding check flagged correct answers.**
- The model wrote "(2026 report)", taking the year from the source label, and the check only looked at the passage text. *Fix:* the source label counts as grounded text.
- "38.4%" from a calculator result of 38.4446 was flagged. *Fix:* a rounded version of a source number counts. Both have tests.

**Derived numbers are flagged, and that's correct.** The model added Amazon's Scope 1+2+3 to get 80.85 and subtracted two PUEs to get 0.05. Both are right, but neither is in the source, so the check flags them. Unverified LLM arithmetic is a reason for a calculator tool.

**The model was right and the answer key was wrong.** For Microsoft's FY25 total emissions the model said 20,290,000 tCO2e, and I'd marked it wrong. Microsoft publishes two totals: Table 1A under standard accounting (21.1M) and Table 1B "with management's criteria" (20.3M), which the main report uses.
- *Fix:* the key accepts both. The change is recorded, and both scores are reported (19/30 before, 20/30 after).

## Extraction

**Quotes shortened with "…".** In a 5-page trial, 6 of 38 facts failed the "quote is on the page" check because the model abbreviated table rows.
- *Fix:* one prompt line ("never shorten it with '...'"). On the full run, 5 of 685 were rejected.

**A check can pass without meaning much.** Some quotes are just fragments ("47.40 2.76"). The value is there, so the check passes, but the quote doesn't show which row or year it came from.
- *Next:* require the row label in quotes from tables.

**Site-level vs company-level.** The schema has no way to say "this is one data centre, not the company", so Apple per-site electricity figures landed in the company metric. This was the one clear error in the hand-checked sample.
- *Next:* add a `scope` field (company / site / region).

## Agent

**Numbers shown in scientific notation.** Formatting fact values with `{value:g}` turned 42,415,800 into "4.24158e+07", so the agent would have seen a rounded number.
- *Found by:* a unit test with a fake client, before any real run.
- *Fix:* full precision with thousands separators.

**A misleading metric.** The report counted questions with more than one model call as "retries", which for an agent is every question.
- *Fix:* report model calls per question for the agent.

**Exact-string grading.** The agent's only two misses were 33.6% where the key wants "34%", and 61.7% where it accepts "62".
- *Next:* a number-aware check or an LLM judge.

## Testing my own checks

**The eval checker was wrong too.** A case-insensitive search for "PUE" matched "PUErto Rico" in an Apple report, so a "not found" question looked answerable.
- *Fix:* whole-word matching.
- *Lesson:* the test set needs testing as well.
