"""Configuration: YAML base + environment overrides."""
from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIGS_DIR = REPO_ROOT / "configs"
DATA_DIR = REPO_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
RESULTS_DIR = REPO_ROOT / "results"
DESIGN_DIR = REPO_ROOT / "design"


class Settings(BaseSettings):
    """Secrets and machine-local values. Everything else lives in YAML."""

    model_config = SettingsConfigDict(
        env_prefix="JANUS_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    sec_user_agent: str = "Janus Research janus@example.com"

    db_host: str = "localhost"
    db_port: int = 55432
    db_name: str = "janus"
    db_user: str = "janus"
    db_password: str = "janus"

    ro_db_user: str = "janus_ro"
    ro_db_password: str = "janus_ro"

    llm_provider: Literal["ollama", "anthropic", "openai"] = "ollama"
    ollama_host: str = "http://localhost:11434"

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password}"
        )

    @property
    def ro_dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.ro_db_user} password={self.ro_db_password}"
        )


class IngestConfig(BaseModel):
    sector: str = "Semiconductors & Semiconductor Equipment"
    fiscal_years: list[int] = Field(default_factory=lambda: [2022, 2023, 2024, 2025])
    forms: list[str] = Field(default_factory=lambda: ["10-K", "10-Q"])
    sec_rate_limit_per_sec: float = 8.0
    restatement_policy: Literal["point_in_time", "latest_restated"] = "point_in_time"
    min_section_chars: int = 400


class ChunkConfig(BaseModel):
    strategy: Literal["section_aware", "fixed"] = "section_aware"
    target_tokens: int = 320
    overlap_tokens: int = 64
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_batch_size: int = 128
    query_prefix: str = "Represent this sentence for searching relevant passages: "


class LLMConfig(BaseModel):
    generator_model: str = "qwen2.5:7b-instruct"
    judge_model: str = "llama3.1:8b"
    temperature: float = 0.0
    max_tokens: int = 900
    timeout_s: int = 240
    seed: int = 1337


class SqlConfig(BaseModel):
    allowed_tables: list[str] = Field(
        default_factory=lambda: [
            "facts", "companies", "filings", "xbrl_facts", "filing_sections",
        ]
    )
    default_limit: int = 50
    max_rows: int = 200
    statement_timeout_ms: int = 10_000
    self_correct_attempts: int = 1  # SPEC §6.2: exactly one retry, never a loop


class RetrievalConfig(BaseModel):
    k: int = 8
    candidate_k: int = 40
    use_reranker: bool = False  # adopted only if POC-03 shows >3pt gain
    reranker_model: str = "BAAI/bge-reranker-base"


class EvalConfig(BaseModel):
    bootstrap_samples: int = 2000
    bootstrap_seed: int = 20240101
    min_cell_n: int = 25  # SPEC §9.4: do not conclude from cells below this


class Config(BaseModel):
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    chunk: ChunkConfig = Field(default_factory=ChunkConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sql: SqlConfig = Field(default_factory=SqlConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    eval: EvalConfig = Field(default_factory=EvalConfig)

    def hash(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(*overlays: str | Path) -> Config:
    """Load configs/base.yaml, then merge any overlay YAMLs on top."""
    raw: dict[str, Any] = {}
    base = CONFIGS_DIR / "base.yaml"
    if base.exists():
        raw = yaml.safe_load(base.read_text()) or {}
    for overlay in overlays:
        p = Path(overlay)
        if not p.exists():
            p = CONFIGS_DIR / str(overlay)
        if not p.exists():
            p = CONFIGS_DIR / "routers" / f"{overlay}.yaml"
        raw = _deep_merge(raw, yaml.safe_load(p.read_text()) or {})
    return Config(**raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
