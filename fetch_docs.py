"""Download the public document set into docs/ and write docs/manifest.csv."""
import csv
import pathlib
import subprocess

DOCS = pathlib.Path(__file__).parent / "docs"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"

# (doc_id, title, company, type, report_year, data_year, url)
# report_year = year published; data_year = the year the numbers describe.
SOURCES = [
    ("google_env_2026", "Google 2026 Environmental Report", "Google", "sustainability_report", 2026, "2025",
     "https://sustainability.google/files/1699a2e19277e478b38a659f00cb38398a836e8c/download-1699a2e1.pdf"),
    ("google_env_2025", "Google 2025 Environmental Report", "Google", "sustainability_report", 2025, "2024",
     "https://www.gstatic.com/gumdrop/sustainability/google-2025-environmental-report.pdf"),
    ("microsoft_esr_2026", "Microsoft 2026 Environmental Sustainability Report", "Microsoft", "sustainability_report", 2026, "FY2025",
     "https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/msc/documents/presentations/CSR/2026-Microsoft-Environmental-Sustainability-Report-PDF.pdf"),
    ("microsoft_datasheet_2026", "Microsoft 2026 Environmental Data Fact Sheet", "Microsoft", "data_sheet", 2026, "FY2025",
     "https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/msc/documents/presentations/CSR/2026-Microsoft-Environmental-Data-Fact-Sheet-PDF.pdf"),
    ("microsoft_esr_2025", "Microsoft 2025 Environmental Sustainability Report", "Microsoft", "sustainability_report", 2025, "FY2024",
     "https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/msc/documents/presentations/CSR/2025-Microsoft-Environmental-Sustainability-Report.pdf"),
    ("meta_sr_2025", "Meta 2025 Sustainability Report", "Meta", "sustainability_report", 2025, "2024",
     "https://sustainability.atmeta.com/asset/2025-sustainability-report"),
    ("amazon_sr_2025", "Amazon 2025 Sustainability Report", "Amazon", "sustainability_report", 2026, "2025",
     "https://sustainability.aboutamazon.com/2025-amazon-sustainability-report.pdf"),
    ("amazon_sr_2024", "Amazon 2024 Sustainability Report", "Amazon", "sustainability_report", 2025, "2024",
     "https://sustainability.aboutamazon.com/2024-amazon-sustainability-report.pdf"),
    ("apple_epr_2026", "Apple Environmental Progress Report 2026", "Apple", "sustainability_report", 2026, "FY2025",
     "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2026.pdf"),
    ("apple_epr_2025", "Apple Environmental Progress Report 2025", "Apple", "sustainability_report", 2025, "FY2024",
     "https://www.apple.com/environment/pdf/Apple_Environmental_Progress_Report_2025.pdf"),
]


def fetch(url: str, dest: pathlib.Path) -> int:
    # curl uses the system certificate store, which python.org builds of Python don't
    subprocess.run(["curl", "-sSfL", "-A", UA, "-m", "300", "-o", str(dest), url], check=True)
    return dest.stat().st_size


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    rows = []
    for doc_id, title, company, doc_type, report_year, data_year, url in SOURCES:
        dest = DOCS / f"{doc_id}.pdf"
        try:
            size = dest.stat().st_size if dest.exists() else fetch(url, dest)
            status = "ok"
        except Exception as e:  # keep going; record the failure in the manifest
            size, status = 0, f"failed: {e}"
        print(f"{status:10} {size/1e6:6.1f} MB  {dest.name}")
        rows.append(dict(doc_id=doc_id, title=title, company=company, type=doc_type, report_year=report_year,
                         data_year=data_year, file=dest.name, url=url, bytes=size, status=status))
    with open(DOCS / "manifest.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
