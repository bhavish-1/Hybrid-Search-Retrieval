# Hybrid-Search-Retrieval

**Per-query routing between SQL and semantic retrieval over SEC filings — benchmarked
against always-both and an oracle upper bound.**

> *Janus: the two-faced god of doorways, who looks in both directions at once.*

**Status: Phase 0 in progress (ingestion foundations).** No results yet. The full
build plan, thesis, and evaluation design live in [`SPEC.md`](SPEC.md); findings will
land in `results/RESULTS.md`.

## Thesis

When the same facts exist in both structured form (XBRL financial data) and unstructured
form (10-K narrative), a per-query router that predicts which retrieval mode is needed
recovers most of the accuracy of always-running-both at a fraction of the cost — and the
queries that actually separate systems are the *sequential* ones, where a SQL result must
become the filter for the document search.

## What exists so far

| Piece | State |
|---|---|
| Postgres 16 + pgvector via docker-compose | working |
| Schema + migrations (§4.6) | applied |
| Read-only SQL role (§4.7) | applied, writes verified blocked |
| Polite disk-cached SEC EDGAR client | working, endpoints verified live |
| Company set: 20 semiconductor filers, FY2022–25 | `configs/companies.yaml` |
| Concept-mapping layer (31 canonical metrics) | `configs/concept_mapping.yaml` |
| Fiscal-period derivation for 52/53-week filers | partial — 12/15 cases passing |

Still to build: XBRL loader, filing downloader, Item segmentation, chunking/embedding,
the labelled query set, all four execution modes, five routers, and the eval harness.

## Verified data notes

SEC endpoints confirmed live on 2026-08-30:
`www.sec.gov/files/company_tickers.json`, `data.sec.gov/submissions/CIK##########.json`,
`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, and the EDGAR archive path for
filing documents. A descriptive `User-Agent` is required; the client rate-limits and
caches every artifact to disk so re-runs never re-hit the network.

**Revenue really is tagged three ways** across these 20 filers —
`RevenueFromContractWithCustomerExcludingAssessedTax` (18/20),
`Revenues` (16/20), `SalesRevenueNet` (15/20) — which is why the concept-mapping layer
resolves by priority rather than trusting any single tag.

**Restatement convention: point-in-time (as-originally-filed).** Gold answers must use
the same convention.

## Setup

```bash
cp .env.example .env          # set JANUS_SEC_USER_AGENT to your name + email
docker compose up -d
uv venv --python 3.11 && uv pip install -e ".[dev]"
```

## License

Copyright (C) 2026 Bhavish Pothuraju.

Janus is free software, licensed under the **GNU General Public License v3.0 or later**.
You may redistribute and modify it under those terms. It is distributed WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See [`LICENSE`](LICENSE) for the full text, or
<https://www.gnu.org/licenses/gpl-3.0.html>.

GPLv3 is copyleft: anything distributed that builds on this code must also be released
under the GPL. If you want the routing components usable inside closed-source work,
that is the wrong licence and it should be changed before the repo goes public.

SEC filing data ingested by this project is US public domain and is not covered by the
above; the `data/` cache is not redistributed.
