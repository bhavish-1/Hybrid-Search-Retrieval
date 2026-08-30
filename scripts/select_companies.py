"""One-off: resolve the candidate semiconductor tickers to CIKs, verify SIC + filing
coverage from SEC submissions, and write configs/companies.yaml."""
import sys
sys.path.insert(0, "src")
import yaml
from janus.ingest.sec_client import SecClient
from janus.config import CONFIGS_DIR

CANDIDATES = [
    "NVDA", "AMD", "INTC", "AVGO", "QCOM", "TXN", "MU", "ADI", "MRVL", "NXPI",
    "ON", "MCHP", "SWKS", "QRVO", "MPWR", "LSCC", "AMAT", "LRCX", "KLAC", "TER",
]
YEARS = {2022, 2023, 2024, 2025}

c = SecClient()
tick = c.company_tickers()
by_ticker = {v["ticker"]: v for v in tick.values()}

rows = []
for t in CANDIDATES:
    if t not in by_ticker:
        print(f"  !! {t} not in company_tickers.json"); continue
    cik = str(by_ticker[t]["cik_str"]).zfill(10)
    sub = c.submissions(cik)
    m = sub["filings"]["merged"]
    n = sum(
        1 for i in range(len(m["form"]))
        if m["form"][i] in ("10-K", "10-Q") and int(m["filingDate"][i][:4]) in
        {y for y in range(min(YEARS), max(YEARS) + 2)}
    )
    rows.append({
        "ticker": t, "cik": cik, "name": sub["name"],
        "sic": sub["sic"], "sic_description": sub["sicDescription"],
        "fiscal_year_end": sub.get("fiscalYearEnd"), "filings_in_window": n,
    })
    print(f"  {t:6s} cik={cik} sic={sub['sic']:5s} fye={sub.get('fiscalYearEnd')} "
          f"filings={n:3d}  {sub['sicDescription'][:42]}  {sub['name'][:32]}")

rows.sort(key=lambda r: r["ticker"])
out = {
    "sector": "Semiconductors & Semiconductor Equipment",
    "sector_rationale": (
        "Same-sector filers share vocabulary (wafer, foundry, design win, inventory "
        "digestion) and report comparable metrics, so cross-company queries are "
        "meaningful. The 2022-2025 window spans both the 2023 memory/analog downturn "
        "and the AI-accelerator boom, producing large real revenue swings in both "
        "directions -- which is what makes type-D ('largest decline, and why?') "
        "queries have verifiable, non-trivial answers."
    ),
    "companies": rows,
}
(CONFIGS_DIR / "companies.yaml").write_text(yaml.safe_dump(out, sort_keys=False, width=100))
print(f"\nwrote {len(rows)} companies; network={c.stats['network']} cache={c.stats['cache']}")
