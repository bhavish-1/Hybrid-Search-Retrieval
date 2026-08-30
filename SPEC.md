# Janus — Build Specification

> **Repository name:** `Hybrid-Search-Retrieval`
> **Repo description (GitHub "About" field):** *Per-query routing between SQL and semantic retrieval over SEC filings — benchmarked against always-both and an oracle upper bound.*
> **Owner:** Bhavish Pothuraju
> **Spec version:** 1.0
> *(Janus: the two-faced god of doorways, who looks in both directions at once.)*

---

## READ THIS FIRST (instructions to the implementing agent)

You are building this project end to end. The human who owns this repo should not have
to write code or make architectural decisions. Everything you need is in this document.

**Rules you must follow:**

1. **Do not skip Phase 0.** The evaluation harness and the labelled query set exist
   before any routing code. A phase is not complete until its acceptance criterion passes.
2. **Every phase ends with a number written to `results/`.**
3. **Verify all external API details before building against them.** Section 4 describes
   SEC EDGAR and XBRL as understood at spec-writing time. Endpoint paths, file formats,
   rate limits, and terms of use **must be confirmed from SEC's own developer
   documentation before you write the fetcher.** If reality differs, follow reality and
   record it in `design/01_data.md`.
4. **Do not add features not in this spec.** No web UI, no chat interface, no
   LangChain/LlamaIndex, no multi-tenancy. This is a research harness. Ideas go in
   `design/99_open_questions.md`.
5. **Ask the human only at the checkpoints marked `HUMAN CHECKPOINT`.** There are four.
6. **Commit per phase, on the branch named in Section 8.**

---

## 1. Thesis

> **When the same facts exist in both structured form (XBRL financial data) and
> unstructured form (10-K narrative), a per-query router that predicts which retrieval
> mode is needed recovers most of the accuracy of always-running-both at a fraction of
> its cost and latency — and the queries that break every single-pass system are the
> ones with a sequential dependency, where a SQL result must become the filter for the
> document search.**

Two falsifiable sub-claims:

- **S1 — Routing is cheaply learnable.** A fine-tuned small classifier matches a
  zero-shot LLM router's routing accuracy at roughly 1/100th the cost and ~5ms instead
  of a full API round-trip. If the small classifier can't get close, the honest
  conclusion is "just use the LLM," and that is still a result.
- **S2 — Sequential-hybrid is the real gap.** Queries of the form *"which quarter had
  the largest decline, and what reason did management give?"* require SQL first, then a
  document search filtered by SQL's answer. Systems that fire both retrievers in
  parallel cannot answer these. **Measure what fraction of realistic analyst questions
  are sequential, and how badly parallel-hybrid does on them.**

**What would prove the thesis wrong:** if routing recovers <85% of always-both accuracy
while costing >80% of its tokens, routing is not worth the complexity. Report that
plainly. A clean negative result beats an unexamined positive one.

---

## 2. Scope

### In scope
- Ingestion of XBRL facts and 10-K/10-Q narrative sections for a bounded company set
- A labelled query set spanning five query types (§4.4)
- Two retrieval paths: text-to-SQL, and semantic retrieval over filing sections
- Four execution modes: sql-only, vector-only, parallel-hybrid, sequential-hybrid
- Five router implementations, including an oracle upper bound
- SQL safety and self-correction machinery
- Answer synthesis with per-claim attribution
- Full evaluation harness, ablations, and a written result

### Explicitly OUT of scope — do not build these
- A chat interface, web UI, or conversational memory (that is a different project)
- Agent frameworks — write the ~300 lines of orchestration directly
- More than one sector, or more than ~20 companies
- Real-time data, streaming filings, or scheduled ingestion
- Fine-tuning any LLM (the *router classifier* is fine-tuned; no LLM is)
- Graph retrieval (see §13 — a deliberate, optional Phase 9)

### Definition of done
`results/RESULTS.md` contains the filled router comparison table (§9.6), the per-query-type
breakdown, the cost/accuracy tradeoff figure, and the SQL-correctness analysis — all
regenerable by `make results`.

---

## 3. Tech stack — decisions and justifications

| Concern | Choice | Why this over the alternative | Cost |
|---|---|---|---|
| Language | Python 3.11 | — | — |
| Dep manager | `uv` | Fast, single lockfile | Newer tool |
| CLI | `typer` | Type hints become the CLI | — |
| Config | YAML + `pydantic-settings` | Router variants differ only by params | — |
| Relational store | PostgreSQL 16 (docker) | XBRL facts are genuinely relational; the SQL path needs a real query planner | Container |
| Vector store | **pgvector on the same Postgres** | Metadata filtering (`company`, `fiscal_period`, `item_section`) is required for the sequential path and is a plain SQL `WHERE`. Two stores would mean joining across services | Slower at 10M+ vectors; irrelevant here |
| SQL validation | **`sqlglot`** | Parses to an AST so you can *prove* a query is read-only and has a LIMIT, rather than regex-matching for `DROP`. Regex-based SQL guards are trivially bypassed | Extra dep |
| Embeddings | `sentence-transformers`, `BAAI/bge-small-en-v1.5` | Local, free; we embed tens of thousands of sections | 384-dim |
| Reranker | `BAAI/bge-reranker-base` | Adopt only if POC-03 shows >3pt recall gain | Latency |
| Generation LLM | Provider-abstracted: Ollama (default `qwen2.5:7b-instruct`) \| Anthropic \| OpenAI \| Azure | Local default keeps iteration free; API for final numbers | Local is weaker at SQL |
| Judge LLM | A strong API model, **different from the generator** | Never judge with the model that generated | API cost |
| Router classifier | `distilbert-base-uncased` fine-tuned | The whole point is beating the LLM router on cost | Needs labels (oracle provides them) |
| HTML parsing | `selectolax` + hand-written Item segmentation | `BeautifulSoup` is slower and filings are large; Item boundaries need custom logic regardless | Manual work |
| XBRL parsing | Direct JSON/TSV handling — **no XBRL library** | The Company Facts API and bulk datasets are already parsed; a full XBRL library is overkill | — |
| Results | JSONL + `pandas` | Append-only, survives mid-run crashes | — |
| Testing | `pytest` | — | — |
| Lint | `ruff` | — | — |

**Determinism:** `temperature=0` everywhere, fixed seeds, and a run manifest (§9.5)
recording model versions, config hash, git SHA, and dataset checksum.

---

## 4. Data

### 4.1 Sources — VERIFY BEFORE BUILDING

**⚠️ The following is the spec author's understanding and may be inaccurate in its
specifics.** Before writing the fetcher, confirm from SEC's own developer documentation:
current endpoint paths, response schemas, bulk-file formats and column names, rate
limits, and the required request headers. **Write what you actually find into
`design/01_data.md` and build against that.**

Understanding at spec time:

- **Structured (XBRL).** The SEC publishes machine-readable financial facts tagged to
  the **us-gaap taxonomy**. Two access routes are believed to exist: a per-company
  "Company Facts" JSON endpoint under `data.sec.gov`, and quarterly bulk "Financial
  Statement Data Sets" distributed as ZIPs containing tab-separated files
  (approximately `sub`, `num`, `pre`, `tag`). Prefer the bulk sets for building the
  corpus and the API for spot checks.
- **Unstructured (filings).** 10-K and 10-Q documents are available as HTML from the
  EDGAR archives, discoverable per company via submission metadata.

**Access etiquette (confirm current rules):** SEC requires a descriptive `User-Agent`
header identifying the requester, and enforces a request rate limit. Build a polite
fetcher with a configurable rate cap, exponential backoff, and **aggressive local disk
caching from day one** — you will re-run ingestion many times and should hit the network
once per artifact.

### 4.2 Corpus scope — deliberately small

**15–20 companies, one sector, 3–4 fiscal years, 10-K and 10-Q only.** Roughly 200–300
filings and a few hundred thousand XBRL facts.

One sector matters: same-sector companies share vocabulary and comparable metrics, which
makes cross-company queries meaningful rather than nonsense. Pick a sector with clean,
comparable reporting — record the choice and the reason in `design/01_data.md`.

### 4.3 Known data hazards — read this section twice

- **Concept tagging is inconsistent.** The same economic quantity may be tagged
  differently across filers and across years (revenue is the classic case, with several
  plausible us-gaap concept names in circulation). **You must build a concept-mapping
  layer** that normalizes ~20–30 key metrics to canonical names, with the mapping stored
  as a reviewable YAML file rather than buried in code. This is real data engineering and
  it is worth its own resume line.
- **Restatements.** A figure filed in one period may differ from the same period's figure
  as it appears in a later filing. **Decide explicitly** whether the store is
  point-in-time (as-originally-filed) or latest-restated, record the decision, and make
  sure the ground-truth answers in your query set use the same convention. Getting this
  wrong silently makes correct answers look wrong.
- **Filing HTML is hostile.** Inline styles, deeply nested tables, no semantic structure.
  Item boundaries (Item 1A, Item 7, Item 3, etc.) are found by heading heuristics and
  filers format them inconsistently. **Budget real time here** — section extraction
  quality determines chunking quality, which determines everything downstream.
- **Units and scaling.** Values may be reported in different scales; the XBRL fact
  carries unit information. Normalize on ingest and store the raw value alongside.
- **Fiscal vs calendar periods.** Companies have different fiscal year-ends. Store both
  the fiscal label and the actual date range, or cross-company period comparisons break.

### 4.4 Query taxonomy — this drives the entire architecture

| Type | Name | Example shape | Correct mode |
|---|---|---|---|
| **A** | Pure structured | *"What was total revenue in Q3 FY2024?"* | SQL only |
| **B** | Pure unstructured | *"What supply-chain risks does management cite?"* | Vector only |
| **C** | Parallel hybrid | *"How did revenue perform in Q3, and what did management say about it?"* | Both, independently |
| **D** | **Sequential hybrid** | *"Which quarter had the largest revenue decline, and what explanation was given?"* | SQL **first**, its result filters the vector query |
| **E** | Verification | *"Does the figure discussed in the narrative match the reported value?"* | Both, then reconcile and report conflicts |

**Types D and E are where this project earns its existence.** Nearly every published
hybrid-RAG demo handles A, B, and C and silently fails D and E. Make sure your query set
is not weighted toward the easy ones — record the per-type counts in `design/01_data.md`.

### 4.5 The labelled query set — this is the real bottleneck

You need **200–300 queries**, each with: the natural-language question, the query type
label, the gold answer, the gold route (which mode(s) *should* have been used), and
where applicable the gold SQL result value and the gold supporting section(s).

Process:
1. Hand-write **40–50 seed queries** spread deliberately across all five types, grounded
   in the actual ingested data (you must be able to verify each answer).
2. Use an LLM to generate variations **grounded in real rows and real sections** — pass
   actual data into the prompt, never generate from thin air.
3. **Hand-verify every generated item.** Generated ground truth is wrong often enough to
   poison the entire evaluation. This step is not optional and cannot be skipped.

> **HUMAN CHECKPOINT 2** sits here. Verification is genuinely the human's work. If an
> LLM verifies LLM-generated ground truth, **say so prominently in the README** — the
> numbers mean something different when the grader is a sibling of the generator.

### 4.6 Schema (Postgres)

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE companies (
    cik             TEXT PRIMARY KEY,
    ticker          TEXT,
    name            TEXT NOT NULL,
    sector          TEXT,
    fiscal_year_end TEXT
);

CREATE TABLE filings (
    id              BIGSERIAL PRIMARY KEY,
    cik             TEXT NOT NULL REFERENCES companies(cik),
    accession        TEXT UNIQUE NOT NULL,
    form_type       TEXT NOT NULL,              -- '10-K' | '10-Q'
    filed_date      DATE NOT NULL,
    period_end      DATE NOT NULL,
    fiscal_label    TEXT,                       -- e.g. 'FY2024Q3'
    source_url      TEXT NOT NULL
);

-- Canonicalised financial facts. `concept_raw` preserves what the filer actually used.
CREATE TABLE xbrl_facts (
    id              BIGSERIAL PRIMARY KEY,
    cik             TEXT NOT NULL REFERENCES companies(cik),
    filing_id       BIGINT REFERENCES filings(id),
    concept_raw     TEXT NOT NULL,
    concept_canon   TEXT NOT NULL,              -- from the concept-mapping layer
    value_num       NUMERIC NOT NULL,
    unit            TEXT NOT NULL,
    period_start    DATE,
    period_end      DATE NOT NULL,
    fiscal_label    TEXT,
    is_restated     BOOLEAN DEFAULT FALSE
);
CREATE INDEX ON xbrl_facts (cik, concept_canon, period_end);

CREATE TABLE filing_sections (
    id              BIGSERIAL PRIMARY KEY,
    filing_id       BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
    item_code       TEXT NOT NULL,              -- '1A' | '7' | '3' | ...
    item_title      TEXT,
    content         TEXT NOT NULL,
    char_count      INT
);

CREATE TABLE section_chunks (
    id              BIGSERIAL PRIMARY KEY,
    section_id      BIGINT NOT NULL REFERENCES filing_sections(id) ON DELETE CASCADE,
    cik             TEXT NOT NULL,              -- denormalised for filtering
    fiscal_label    TEXT,                       -- denormalised for the sequential path
    item_code       TEXT NOT NULL,
    chunk_index     INT NOT NULL,
    content         TEXT NOT NULL,
    embedding       VECTOR(384) NOT NULL,
    embed_model     TEXT NOT NULL
);
CREATE INDEX ON section_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON section_chunks (cik, fiscal_label, item_code);
```

**The denormalised `cik` / `fiscal_label` / `item_code` columns on `section_chunks` are
load-bearing.** The sequential path works by taking SQL's answer ("Q2 FY2023") and using
it as a metadata filter on the vector search. Without these, that filter requires a join
and the whole design gets awkward.

### 4.7 A read-only role for the SQL path

Create a dedicated Postgres role with `SELECT`-only grants on the fact tables and a
`statement_timeout`. The generated-SQL executor connects **only** as this role. Defence
in depth: `sqlglot` validation *and* database-enforced permissions. Never rely on the
prompt to keep generated SQL safe.

---

## 5. High-level design

```
                                question
                                    │
                                    ▼
                            ┌──────────────┐
                            │    ROUTER    │  → RouteDecision
                            └──────────────┘     {mode, k, needs_sql_first}
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
    mode=SQL                   mode=VECTOR              mode=PARALLEL / SEQUENTIAL
        │                           │                           │
        ▼                           ▼                           ▼
┌───────────────┐          ┌────────────────┐      ┌────────────────────────────┐
│  SQL PATH     │          │  VECTOR PATH   │      │  SEQUENTIAL:               │
│  NL→SQL       │          │  embed(q)      │      │   1. run SQL path          │
│  sqlglot AST  │          │  ANN + filters │      │   2. extract entities from │
│   validate    │          │  rerank        │      │      the SQL result        │
│  read-only    │          └────────────────┘      │   3. run VECTOR path with  │
│   execute     │                                  │      those as filters      │
│  self-correct │                                  │  PARALLEL: run both, merge │
│   on error    │                                  └────────────────────────────┘
└───────────────┘
        │                           │                           │
        └───────────────────────────┼───────────────────────────┘
                                    ▼
                         ┌─────────────────────┐
                         │     SYNTHESIS       │  answer + per-claim attribution
                         │  (+ reconciliation  │  (type E surfaces conflicts
                         │   for type E)       │   rather than hiding them)
                         └─────────────────────┘
                                    │
                                    ▼
                          JUDGE ──► results/*.jsonl
```

**Component contracts:**

| Component | Responsibility | Must NOT |
|---|---|---|
| `Router` | question → `RouteDecision` | execute any retrieval |
| `SqlGenerator` | question (+ schema card) → candidate SQL | execute it |
| `SqlValidator` | AST-check: read-only, has LIMIT, tables exist | rewrite intent |
| `SqlExecutor` | run validated SQL as the read-only role, with timeout | generate or validate |
| `SqlSelfCorrector` | on execution error, one retry with the error appended | loop indefinitely |
| `VectorRetriever` | query + metadata filters → ranked chunks | decide filters itself |
| `EntityExtractor` | SQL result rows → filter values for the sequential path | call the retriever |
| `Synthesizer` | evidence bundle → answer + attribution | fetch evidence |
| `Reconciler` | structured value vs narrative claim → agree / conflict | be silent on conflict |
| `Judge` | (question, gold, prediction) → verdict | be the same model as the synthesizer |
| `EvalRunner` | orchestrate, write JSONL | contain routing logic |

Every execution mode implements one interface so the runner is mode-agnostic:

```python
class QueryMode(Protocol):
    name: str
    def answer(self, question: str, budget: TokenBudget) -> AnswerBundle: ...
```

---

## 6. Low-level design

### 6.1 Core types

```python
class RouteDecision(BaseModel):
    mode: Literal["sql", "vector", "parallel", "sequential", "none"]
    retrieval_k: int = 8
    metadata_filters: dict[str, str] = {}
    reasoning: str | None = None          # populated by the LLM router only

class SqlAttempt(BaseModel):
    sql: str
    valid: bool
    validation_error: str | None
    executed: bool
    execution_error: str | None
    rows: list[dict] | None
    attempt_number: int

class Evidence(BaseModel):
    kind: Literal["sql_rows", "chunk"]
    content: str
    source_ref: str                       # accession + item_code + chunk_index, or the SQL
    score: float | None = None

class AnswerBundle(BaseModel):
    answer: str
    evidence: list[Evidence]
    route: RouteDecision
    sql_attempts: list[SqlAttempt] = []
    conflicts: list[str] = []             # type E: surfaced, never hidden
    tokens_total: int
    latency_ms: float

class EvalRecord(BaseModel):
    run_id: str
    question_id: str
    query_type: Literal["A", "B", "C", "D", "E"]
    mode_used: str
    gold_mode: str
    route_correct: bool
    predicted: str
    gold: str
    answer_correct: bool
    sql_executed_ok: bool | None
    sql_result_correct: bool | None
    retrieval_recall_at_k: float | None
    tokens_total: int
    latency_ms: float
    oracle_best_mode: str | None = None
```

### 6.2 The SQL path — where the real engineering is

**The core problem: wrong SQL fails silently.** It returns a plausible number that is
simply incorrect, and nothing in the system flags it. Every design choice below exists
because of that.

**Schema card.** The NL→SQL prompt receives a compact, hand-curated description of the
tables: column names, types, 2–3 sample rows per table, the canonical concept list, and
explicit notes on the traps ("`fiscal_label` is the filer's fiscal period, not calendar";
"use `concept_canon`, never `concept_raw`"). Store it as `configs/schema_card.md` — a
reviewable artifact, not an f-string in code.

**Validation via AST, not regex.** Parse with `sqlglot`. Reject unless: exactly one
statement; it is a `SELECT`; every referenced table is on the allowlist; no DDL/DML
nodes anywhere in the tree; a `LIMIT` is present (inject a default if absent). Regex
guards for `DROP` are trivially bypassed and give false confidence.

**Execution.** Read-only role, `statement_timeout` from config, row cap.

**Self-correction — exactly one retry.** On execution error, re-prompt with the failed
SQL and the database's error message appended. **One retry, not a loop.** Log every
attempt in `SqlAttempt` — the retry-success rate is a reported metric, not an
implementation detail.

**Never grade the SQL string.** There are many correct queries for one question.
Grade the *result value* against the gold value. String-matching generated SQL against a
reference query is the most common way this class of project produces meaningless
numbers.

### 6.3 The sequential path (type D) — the differentiator

```
1. Run the SQL path with a sub-question derived from the original.
   ("Which quarter had the largest revenue decline?" → SQL)
2. EntityExtractor reads the result rows and pulls filter values:
   {cik: '...', fiscal_label: 'FY2023Q2'}
3. Run the vector path with those as metadata filters, and with the
   *narrative* half of the original question as the query text.
   ("what explanation was given" + filter fiscal_label=FY2023Q2, item_code='7')
4. Synthesize from both.
```

**Failure handling:** if step 1 returns no rows or fails validation twice, fall back to
parallel mode and **record the fallback**. The fallback rate on type-D queries is a
reported number.

**Sub-question decomposition** is itself an LLM call. Keep it constrained: emit exactly
two sub-questions, one structured and one narrative, as validated JSON.

### 6.4 Reconciliation (type E)

Given a structured value and a narrative claim about the same quantity, emit one of:
`agree`, `conflict`, or `insufficient`. **A conflict is reported to the user, never
silently resolved.** The whole point of a verification query is to surface disagreement.

Watch for false conflicts from unit or scale mismatches — normalize before comparing,
and log any comparison where units differed.

### 6.5 Router implementations

| Version | Mechanism | Cost | Built in |
|---|---|---|---|
| `always_parallel` | Never routes; always runs both, merges | Highest | Phase 3 |
| `heuristic` | Rules over surface features: numeric/temporal tokens, aggregation words ("largest", "total"), narrative verbs ("said", "cite", "explain"), superlative + causal co-occurrence (a strong type-D signal) | ~0 | Phase 5a |
| `llm` | One constrained classification call over the 5-way label set | 1 call/query | Phase 5b |
| `learned` | DistilBERT fine-tuned on oracle labels | ~5ms | Phase 5c |
| `gated` | `learned`; if max softmax < threshold τ, escalate to `llm`. τ swept | tunable | Phase 5d |
| `oracle` | Runs every mode, picks best post hoc | N× | Phase 4 |

`learned` training data comes free from Phase 4's oracle. **Split by company, not by
query** — queries about the same company share vocabulary and will leak.

The `gated` router is the interesting one for the writeup: it gives a tunable curve
between cost and accuracy rather than a single point.

---

## 7. POC notebooks

Each answers one question, has a decision rule and a time box, and ends with a markdown
cell recording the decision. **Commit with outputs** — they are evidence that choices
were tested rather than guessed. Conclusions move into `src/`; notebooks are never imported.

| # | Notebook | Question | Decision rule | Box |
|---|---|---|---|---|
| 01 | `01_edgar_exploration` | What do the filings and XBRL data actually look like? Concept-name variance, section-extraction success rate, restatement frequency | Output feeds `design/01_data.md` and the concept-mapping YAML | 5h |
| 02 | `02_section_segmentation` | Which Item-boundary heuristic works? | ≥90% of filings segment into recognisable Items; report the failures | 4h |
| 03 | `03_chunking_and_embeddings` | Section-aware vs fixed-size chunking; bge-small vs bge-base; rerank worth it? | Highest recall@10 for gold sections; adopt bigger/rerank only on >3pt gain | 4h |
| 04 | `04_text_to_sql_spike` | Can the local model write correct SQL against this schema? What does the schema card need? | ≥70% result-accuracy on 20 hand-written type-A queries before Phase 2 | 5h |
| 05 | `05_judge_agreement` | Does the LLM judge agree with a human on answer correctness? | Hand-label 50; require ≥90% agreement or revise the judge prompt | 3h |
| 06 | `06_sequential_probe` | On 10 hand-built type-D queries, does parallel-hybrid actually fail where sequential succeeds? | Confirms or kills sub-claim S2 before Phase 3 is built | 3h |
| 07 | `07_results_analysis` | Final tables and all figures | Produces everything in `results/figures/` | 5h |

**Notebooks 04, 05 and 06 gate later phases.** If the local model cannot write usable
SQL (04), switch the default generator to an API model and record the cost implication.
If the judge is unreliable (05), every number downstream is unreliable. If parallel
already handles type D (06), sub-claim S2 is dead and the thesis needs rewording —
better to learn that in week two than week ten.

---

## 8. Build phases

Each phase: branch, work, acceptance criterion, merge. **Do not start a phase before its
predecessor's criterion passes.**

---

**PHASE 0 — Ingestion and corpus** · `phase-0-ingest`

Repo skeleton, `uv`, docker-compose (Postgres+pgvector), migrations, config loading,
polite cached SEC fetcher, XBRL loader, concept-mapping YAML, filing downloader, Item
segmentation, section chunking + embedding. Notebooks 01 and 02.

*Acceptance:* `make ingest` populates all tables for the chosen company set; a
`make corpus-report` command prints per-company filing counts, fact counts, section
extraction success rate, and concept-mapping coverage.

> **HUMAN CHECKPOINT 1** — show the human the corpus report, the sector choice, the
> concept-mapping YAML, and the point-in-time-vs-restated decision. This is where they
> learn what the data actually is.

---

**PHASE 1 — Query set and harness** · `phase-1-queryset`

Eval harness (`EvalRunner`, `Judge`, JSONL writer, run manifest), notebook 05, and the
labelled query set per §4.5. Also: `no_retrieval` baseline (question alone, no evidence)
— the floor.

*Acceptance:* `data/queries.jsonl` with ≥200 verified queries and per-type counts
recorded; `make baseline-none` writes a results file; `make results` prints a table
broken out by query type.

> **HUMAN CHECKPOINT 2** — query-set verification (§4.5). Genuinely the human's work.

---

**PHASE 2 — SQL path** · `phase-2-sql`

Notebook 04 first. Then schema card, `SqlGenerator`, `sqlglot` validator, read-only
executor, one-shot self-corrector.

*Acceptance:* `sql_only` mode runs the full query set. Report **three separate numbers**:
SQL validation pass rate, SQL execution success rate, and SQL *result* accuracy on type-A
queries. Plus the self-correction success rate. A test asserting that a `DROP`-containing
generated query is rejected by the validator **and** by the DB role must pass.

---

**PHASE 3 — Vector path, parallel, sequential** · `phase-3-modes`

Notebooks 03 and 06. Then `vector_only`, `parallel`, and `sequential` modes, plus the
reconciler for type E.

*Acceptance:* four more rows in the table. **Explicitly test sub-claim S2:** parallel vs
sequential accuracy on type-D queries specifically, with the type-D fallback rate
reported. Also `always_parallel` as the upper-bound row.

---

**PHASE 4 — Oracle** · `phase-4-oracle`

Run every mode on every query; record which was best. Recompute prior rows as
*% of oracle*. Emit router training data.

*Acceptance:* `results/oracle.jsonl`; a "% of oracle" column in the table;
`data/router_training.jsonl` split **by company**.

---

**PHASE 5 — Routers** · `phase-5a-heuristic`, `-5b-llm`, `-5c-learned`, `-5d-gated`

Four sub-phases, four branches, in order.

*Acceptance:* per router — routing accuracy vs gold route, answer accuracy, % of oracle,
mean tokens, p95 latency. Plus the headline figure: answer accuracy against token cost,
with `always_parallel` and `oracle` as reference points, and the `gated` router drawn as
a curve across τ.

> **HUMAN CHECKPOINT 3** — the thesis is confirmed or refuted here. Show the tradeoff
> figure before writing conclusions. **If routing lost, say so.**

---

**PHASE 6 — Error analysis and ablations** · `phase-6-ablations`

Ablations: accuracy vs corpus size (5/10/20 companies); accuracy vs retrieval `k`;
accuracy with and without the reranker; SQL accuracy with and without the schema card;
sequential accuracy with and without self-correction.

Plus a genuine **error taxonomy**: sample ~50 wrong answers, categorise the cause
(wrong route / correct route but wrong SQL / correct SQL but wrong synthesis / retrieval
miss / bad gold label), and report the distribution.

*Acceptance:* ablation figures in `results/figures/`; the error-taxonomy table in
`RESULTS.md`. **The error taxonomy is the most persuasive artifact in the repo** — it is
what a senior reviewer reads to decide whether you understand your own system.

---

**PHASE 7 — Write-up** · `phase-7-writeup`

Notebook 07, final `RESULTS.md`, README rewrite (§11), architecture diagram,
`make results` regenerating everything from stored runs.

> **HUMAN CHECKPOINT 4** — the human writes or edits "Limitations". Must include:
> single-sector/single-corpus generalization; query-set construction and who verified it;
> judge reliability from notebook 05; the restatement convention chosen and what it
> excludes; and the fact that gold routes are the spec author's judgment, not ground
> truth from nature.

---

## 9. Evaluation design

### 9.1 Baselines (all mandatory)
1. **`no_retrieval`** — question alone. The floor.
2. **`sql_only`** — every query forced through SQL.
3. **`vector_only`** — every query forced through retrieval.
4. **`always_parallel`** — both, every time. Practical ceiling.
5. **`oracle`** — best mode per query, post hoc. Theoretical ceiling.

Every proposed router is reported against **all five**.

### 9.2 Metrics

**Answer accuracy** — judge verdict, reported **per query type** and overall. The
per-type breakdown is the point; a single overall number hides the entire finding.

**Routing accuracy** — predicted mode vs gold mode; plus agreement with the oracle's
post-hoc best (these differ, and the gap is interesting).

**SQL quality** — three distinct rates, never collapsed into one: validation pass,
execution success, and **result correctness**. Plus self-correction success rate.

**Retrieval quality** — recall@k of gold supporting sections.

**Sequential-specific** — type-D accuracy by mode; sequential fallback rate.

**Verification-specific** — type-E conflict detection precision and recall against
deliberately planted mismatches.

**Cost** — mean prompt tokens per query, LLM calls per query, estimated USD.

**Latency** — p50 and p95 end-to-end, broken out by mode.

### 9.3 The primary result

> **Learned routing achieves __% of `always_parallel` accuracy at __% of its token cost
> and __ms p95, versus __% for sql_only and __% for vector_only. On sequential (type D)
> queries, the sequential mode scores __% against __% for parallel.**

### 9.4 Statistical honesty

Bootstrap 95% CIs over queries for every accuracy number. With ~250 queries split across
five types, per-type cells will be small — **report the n for every cell** and resist
drawing conclusions from cells with n < 25. A table of bare point estimates on small
cells is the clearest possible signal of an inexperienced author.

### 9.5 Run manifest

Every run writes `results/runs/<run_id>_manifest.json`: git SHA, config hash, resolved
config, model names and versions, embedding model, corpus checksum, query-set version,
wall-clock, host.

### 9.6 The results table — write this empty, now, before any code

| Mode / Router | Overall | A (SQL) | B (Vector) | C (Parallel) | D (Sequential) | E (Verify) | SQL result acc | Mean tokens | p95 ms | % of oracle |
|---|---|---|---|---|---|---|---|---|---|---|
| no_retrieval | | | | | | | | | | |
| sql_only | | | | | | | | | | |
| vector_only | | | | | | | | | | |
| parallel (no seq) | | | | | | | | | | |
| always_parallel | | | | | | | | | | |
| router: heuristic | | | | | | | | | | |
| router: llm | | | | | | | | | | |
| router: learned | | | | | | | | | | |
| router: gated (τ=…) | | | | | | | | | | |
| **oracle** | | | | | | | | | | |

---

## 10. Repository structure

```
janus/
├── README.md                     # findings first — see §11
├── SPEC.md                       # this file
├── Makefile
├── pyproject.toml
├── docker-compose.yml
├── .env.example
│
├── design/
│   ├── 00_index.md
│   ├── 01_data.md                # ACTUAL EDGAR/XBRL findings (supersedes §4.1)
│   ├── 02_hld.md
│   ├── 03_lld_sql_path.md
│   ├── 04_lld_vector_path.md
│   ├── 05_lld_router.md
│   ├── 06_evaluation.md
│   └── 99_open_questions.md
│
├── configs/
│   ├── base.yaml
│   ├── schema_card.md            # hand-curated, feeds the NL→SQL prompt
│   ├── concept_mapping.yaml      # raw us-gaap concept → canonical metric
│   ├── companies.yaml            # the chosen sector + company set
│   └── routers/                  # one yaml per router variant
│
├── src/janus/
│   ├── ingest/                   # sec_client, xbrl_loader, filing_loader,
│   │                             # segmentation, concept_mapper, chunking, embedding
│   ├── db/
│   ├── sql_path/                 # generator, validator (sqlglot), executor, corrector
│   ├── vector_path/              # retriever, reranker, filters
│   ├── modes/                    # sql_only, vector_only, parallel, sequential
│   ├── router/                   # heuristic, llm, learned, gated, oracle
│   ├── synthesis/                # synthesizer, reconciler, attribution
│   ├── llm/                      # provider abstraction, judge
│   ├── eval/                     # runner, metrics, bootstrap, error_taxonomy
│   └── cli.py
│
├── notebooks/                    # 01–07, committed WITH outputs
├── tests/                        # incl. test_sql_injection_blocked.py
├── data/                         # gitignored except queries.jsonl + checksums
│   └── queries.jsonl             # the labelled query set — COMMITTED
└── results/
    ├── RESULTS.md
    ├── runs/                     # *.jsonl + manifests (committed)
    └── figures/                  # PNGs (committed)
```

**`data/queries.jsonl` is committed.** It is the most labour-intensive artifact in the
project and the thing that makes the results reproducible. Cached SEC files are not
committed.

---

## 11. README structure (the portfolio artifact)

1. **One-sentence thesis** — the claim from §1, not "a RAG app for SEC filings".
2. **Headline result in the first screen** — the primary number (§9.3), the tradeoff
   figure, and the results table. Findings before setup instructions.
3. **The three findings**, one paragraph each — including any that contradict the
   hypothesis. Sub-claim S2 (sequential queries) goes here if it held.
4. **The error taxonomy table** from Phase 6.
5. **Architecture diagram** — the §5 flow.
6. **How it works** — one short paragraph per component, with the SQL-safety design
   called out explicitly (AST validation + read-only role).
7. **Reproduce it** — `docker compose up`, `make ingest`, `make results`.
8. **What I tried that didn't work** — from the POC notebooks.
9. **Limitations** — human-written (Checkpoint 4).
10. **Notebooks index** — linked, with the decision each produced.

**Suggested resume bullet** (fill from real results):

> Built **Janus**, a hybrid retrieval system over SEC filings that routes each query
> between text-to-SQL over XBRL financial data and semantic search over 10-K narrative,
> including a sequential mode where SQL results filter the document search. Benchmarked
> five routing strategies against an oracle upper bound on a hand-verified 250-query set:
> learned routing recovered **__%** of always-both accuracy at **__%** of token cost,
> and sequential execution improved dependent-query accuracy by **__pt**. Python,
> PostgreSQL/pgvector, sqlglot, PyTorch, DistilBERT, Docker.

---

## 12. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| **SEC endpoints/formats differ from §4.1** | High | Phase 0 verifies first; fetcher isolated behind one interface |
| **Item segmentation fails on many filings** | **High** | Notebook 02 measures it; if <90%, narrow the company set to cleaner filers rather than fighting the HTML |
| **Local model can't write usable SQL** | Medium-high | Notebook 04 gates Phase 2; fall back to an API model and record the cost |
| **Query set is the schedule killer** | **High** | 40–50 hand-written seeds are the true minimum; LLM expansion is an accelerant, not a substitute for verification |
| **Type D/E under-represented** | Medium | Per-type counts are an acceptance criterion in Phase 1, not an afterthought |
| **Concept mapping wrong → silently wrong answers** | Medium-high | Mapping lives in reviewable YAML; spot-check against a handful of filings by hand |
| **Restatement convention mismatch with gold answers** | Medium | Decided and recorded in Phase 0, before any query is written |
| **Judge unreliable** | Medium | Notebook 05 gates everything downstream |
| **Scope creep (esp. adding graph retrieval)** | **High** | §2 out-of-scope is binding; graph is Phase 9 and only after Phase 7 ships |
| **Generated SQL damages the DB** | Low but severe | AST validation **and** read-only role **and** a test that proves both |

**Minimum viable version.** Phases 0–3 alone are a complete project: a real corpus, a
verified query set, five execution modes, and a measured finding about sequential
queries. Phases 4–5 (oracle + routers) are the strongest addition. **Ship 0–3 well
rather than 0–7 badly.**

---

## 13. Optional Phase 9 — graph retrieval (do not start before Phase 7 ships)

Graph-RAG and knowledge-graph experience appeared repeatedly in the job descriptions
that motivated this project. Adding a third retrieval mode is a natural extension: entity
relationships in filings (subsidiaries, shared officers, ownership chains, segment
hierarchies) are genuinely graph-shaped, and "which companies share board members" is a
query type neither SQL nor vector search handles well.

If attempted: Neo4j or Postgres recursive CTEs, a sixth query type (F: relational), and
a fourth mode in the router. **This roughly doubles the router's complexity and requires
extending the labelled query set** — which is exactly why it is fenced off here rather
than folded into the main build. Get the two-mode version finished and measured first.

---

## 14. Honest notes for the human

- **Realistically 10–16 focused weekends** for the full arc, 6–8 for the MVP (Phases 0–3).
- **Phase 0 and Phase 1 produce no working system.** Ingestion and a hand-verified query
  set are unglamorous and they are where this project's value is decided. A brilliant
  router evaluated on a sloppy query set produces meaningless numbers.
- **The error taxonomy in Phase 6 will teach you more than any other phase.** Sampling
  50 wrong answers and categorising why is the thing that turns a demo into a system you
  understand — and it is the section a senior reviewer will read most carefully.
- **The interesting outcome may be negative.** If parallel-hybrid handles type D fine, or
  routing does not pay for itself, report it. A clean negative result, honestly measured,
  is a stronger signal than a tuned win.
