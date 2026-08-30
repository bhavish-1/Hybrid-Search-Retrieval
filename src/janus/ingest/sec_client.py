"""Polite, disk-cached SEC EDGAR client.

Verified against SEC's live endpoints on 2026-08-30 (see design/01_data.md):
  - https://www.sec.gov/files/company_tickers.json
  - https://data.sec.gov/submissions/CIK##########.json
  - https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
  - https://www.sec.gov/Archives/edgar/data/<cik-int>/<accession-nodash>/<doc>

SEC requires a descriptive User-Agent and rate-limits to ~10 req/s. We cache every
artifact to disk on first fetch so re-running ingestion never re-hits the network.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from janus.config import CACHE_DIR, get_settings

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}/{doc}"


class RateLimiter:
    def __init__(self, per_sec: float):
        self._min_interval = 1.0 / per_sec if per_sec > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            sleep_for = self._min_interval - (now - self._last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last = time.monotonic()


class SecClient:
    def __init__(self, rate_limit_per_sec: float = 8.0, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (CACHE_DIR / "sec")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.limiter = RateLimiter(rate_limit_per_sec)
        ua = get_settings().sec_user_agent
        self.client = httpx.Client(
            headers={
                "User-Agent": ua,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=60.0,
            follow_redirects=True,
        )
        self.stats = {"network": 0, "cache": 0}

    # ---------- cache ----------
    def _cache_path(self, url: str, suffix: str) -> Path:
        key = hashlib.sha256(url.encode()).hexdigest()[:24]
        tail = url.rstrip("/").split("/")[-1][:60].replace("?", "_")
        return self.cache_dir / f"{tail}.{key}{suffix}"

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        wait=wait_exponential(multiplier=1.5, min=2, max=45),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _get(self, url: str) -> bytes:
        self.limiter.wait()
        r = self.client.get(url)
        if r.status_code == 429:
            time.sleep(5)
            raise httpx.HTTPError("429 rate limited")
        r.raise_for_status()
        return r.content

    def fetch(self, url: str, suffix: str = ".json") -> bytes:
        """Fetch with aggressive disk caching. Never re-hits the network for a hit."""
        p = self._cache_path(url, suffix)
        if p.exists() and p.stat().st_size > 0:
            self.stats["cache"] += 1
            return p.read_bytes()
        content = self._get(url)
        p.write_bytes(content)
        self.stats["network"] += 1
        return content

    def fetch_json(self, url: str) -> Any:
        return json.loads(self.fetch(url, ".json"))

    # ---------- endpoints ----------
    @staticmethod
    def cik10(cik: str | int) -> str:
        return str(int(cik)).zfill(10)

    def company_tickers(self) -> dict[str, dict]:
        return self.fetch_json(TICKERS_URL)

    def submissions(self, cik: str | int) -> dict:
        """Recent submissions. Older pages live in ['filings']['files'] — merged here."""
        base = self.fetch_json(SUBMISSIONS_URL.format(cik10=self.cik10(cik)))
        recent = base["filings"]["recent"]
        merged = {k: list(v) for k, v in recent.items()}
        for extra in base["filings"].get("files", []):
            url = f"https://data.sec.gov/submissions/{extra['name']}"
            try:
                page = self.fetch_json(url)
            except httpx.HTTPError:
                continue
            for k in merged:
                merged[k].extend(page.get(k, []))
        base["filings"]["merged"] = merged
        return base

    def company_facts(self, cik: str | int) -> dict:
        return self.fetch_json(COMPANYFACTS_URL.format(cik10=self.cik10(cik)))

    def filing_document(self, cik: str | int, accession: str, doc: str) -> str:
        url = ARCHIVE_URL.format(
            cik_int=int(cik), acc_nodash=accession.replace("-", ""), doc=doc
        )
        raw = self.fetch(url, ".htm")
        return raw.decode("utf-8", errors="ignore")

    @staticmethod
    def filing_url(cik: str | int, accession: str, doc: str) -> str:
        return ARCHIVE_URL.format(
            cik_int=int(cik), acc_nodash=accession.replace("-", ""), doc=doc
        )

    def close(self) -> None:
        self.client.close()
