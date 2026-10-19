# The reports

10 public PDFs, 842 pages, about 400,000 words. They're listed in [`data/reports/manifest.csv`](../data/reports/manifest.csv), and `fetch_docs.py` downloads them from their publishers.

## Why these reports

They all cover the same things (data centres, electricity, water and carbon), so the same question can be asked of every company. Most companies have **two editions**, and that's deliberate: the same question can have a different answer depending on which year's report you read, and a reliable system has to notice.

Each report has two years attached to it:
- `report_year`: when it was published.
- `data_year`: the year its numbers describe.

"Google's PUE in 2025" lives in the **2026** report. The search filters use `data_year`.

## What each report covers

| Report | Pages | Notes |
|---|---|---|
| **Google Environmental Report 2026** (2025 data) | 117 | Electricity demand up 37% in a year, driven by AI. 100% of electricity matched with renewable purchases over the year, but about 65% carbon-free hour by hour ("24/7 CFE"). Data-centre PUE 1.09. Over 12 GW of new clean-energy deals. Emissions up 18%, water use up 34% (78% replenished). Large data appendix (p93). |
| **Google Environmental Report 2025** (2024 data) | 120 | Previous edition. CFE 66%, water replenished 64%, PUE also 1.09. Gives a different 2024 data-centre electricity figure from the 2026 edition. |
| **Microsoft Environmental Sustainability Report 2026** (FY25) | 66 | 100% renewable electricity matching. Up to 40 GW of renewable agreements (19 GW online). Water replenished exceeded withdrawals for the first time. Few detailed numbers: those are in the fact sheet. |
| **Microsoft Environmental Data Fact Sheet 2026** | 28 | Tables only: emissions by scope for FY20–FY25, electricity, water, waste. Two emissions totals: Table 1A (21.1M tCO2e) and Table 1B "with management's criteria" (20.3M). |
| **Microsoft Environmental Sustainability Report 2025** (FY24) | 90 | Previous edition. Reports total emissions +23.4% vs the 2020 baseline, which the 2026 fact sheet's restated figures don't reproduce. |
| **Meta Sustainability Report 2025** (2024 data) | 74 | Net-zero operations since 2020, 27 owned data-centre locations, water-positive goal for 2030. PUE and electricity use are in a separate data index, not in this report. |
| **Amazon Sustainability Report 2025** (2025 data) | 51 | Carbon footprint 80.85 MMT CO2e, up 16%. Data-centre PUE 1.14 (cloud industry average 1.25). 75% of the way to water positive. |
| **Amazon Sustainability Report 2024** (2024 data) | 57 | Previous edition. PUE 1.15. Gives 2024 emissions as 68.25 MMT, where the 2025 edition says 69.55. |
| **Apple Environmental Progress Report 2026** (FY25) | 113 | Emissions over 60% below 2015. All 8 Apple-owned data centres water-certified. 100% renewable electricity for offices, stores and data centres since 2018. Mostly about products. |
| **Apple Environmental Progress Report 2025** (FY24) | 126 | Previous edition, same structure. Its tables list years newest-first, unlike Google's. |

## What makes them hard

- **Tables.** The key numbers are in data tables that become flat rows ("Data centers MWh 17,429,800 20,616,500 … 42,415,800") with the years on a different line.
- **Restatements.** Google, Microsoft and Amazon all report different figures for the same past year in different editions.
- **Several definitions of one metric.** Microsoft's two emissions totals; Google's "ambition-based" vs GHG-Protocol emissions; Apple's net vs gross footprint; annual renewable matching vs hourly carbon-free energy.
- **Numbers in a separate document.** Microsoft's main report has almost no totals, and Meta's PUE isn't in its report at all.
- **Page-break debris.** Google repeats a navigation bar on every page, which can land in the middle of a sentence that crosses a page break.
- **Public data only, not redistributed.** The PDFs are gitignored and downloaded from the publishers' own URLs.
