# arXiv Research Briefing Agent

A six-skill agent that, given a natural-language research query (English or Chinese), searches arXiv, ranks results, extracts structured research infomation, builds a paper/author/topic network, and produces a daily briefing plus follow-up Q&A.

---

## Table of contents

- [arXiv Research Briefing Agent](#arxiv-research-briefing-agent)
  - [Table of contents](#table-of-contents)
  - [What the agent does](#what-the-agent-does)
  - [Workflow: a 6-skill pipeline](#workflow-a-6-skill-pipeline)
  - [Directory structure](#directory-structure)
  - [Quick start](#quick-start)
    - [Prerequisites](#prerequisites)
    - [Option A — natural language end-to-end (recommended)](#option-a--natural-language-end-to-end-recommended)
    - [Option B — per-skill slash commands (explicit control)](#option-b--per-skill-slash-commands-explicit-control)
    - [Option C — run the scripts directly (outside Claude Code)](#option-c--run-the-scripts-directly-outside-claude-code)
  - [Skills](#skills)
    - [1. `paper-search` — data entry into arXiv](#1-paper-search--data-entry-into-arxiv)
    - [2. `paper-rank` — BM25 + recency ranking](#2-paper-rank--bm25--recency-ranking)
    - [3. `paper-extract` — rule-based structured extraction](#3-paper-extract--rule-based-structured-extraction)
    - [4. `paper-network` — graph analysis + metrics](#4-paper-network--graph-analysis--metrics)
    - [5. `paper-report` — daily briefing](#5-paper-report--daily-briefing)
    - [6. `follow-up` — grounded Q\&A over the cached run](#6-follow-up--grounded-qa-over-the-cached-run)
    - [Default I/O behavior every skill MUST implement](#default-io-behavior-every-skill-must-implement)
  - [Contributions](#contributions)

---

## What the agent does

Type a natural-language request such as

> *"为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习"*

or

> *"find recent papers on diffusion models for medical imaging from the last 6 months"*

and the agent will:

1. Parse the request into a structured `query.json` (containing search terms, date range, arXiv categories, max_results).
2. Hit the arXiv API, deduplicate, and cache the response.
3. Re-rank the corpus by relevance + recency.
4. Extract structured signals (contribution / method / task / keywords / datasets / evaluation / limitations) for the top-N.
5. Build a paper / author / category / topic graph and a paper-paper projection, and compute centrality / novelty / bridging metrics for every paper.
6. Render a Markdown + JSON daily briefing.
7. Answer follow-up questions (*"tell me more about rank 1"*, *"compare paper A vs B"*) entirely from the cached run, with no extra arXiv calls and no LLM.

The whole run is cached on disk under a single `output/<run_id>/` directory, so the briefing is fully reproducible and the follow-up skill is grounded.

---

## Workflow: a 6-skill pipeline

```
                  paper-search
                       │
                       ▼
                raw_papers.json
                       │
                       ▼
                   paper-rank
                       │
                       ▼
               ranked_papers.json
                       │
            ┌──────────┴──────────┐
            ▼                     ▼
      paper-extract          paper-network ──► graph.json
            │                     │            graph_metrics.json
            ▼                     │
   enriched_papers.json           │
            │                     │
            │                     │
            │                     │
            └──────────┬──────────┘
                       ▼
                   paper-report
                       │
                       ▼
              briefing.md / briefing.json
                       │
                       ▼
                    follow-up
                       │
                       ▼
              answer (stdout) [+ followups.jsonl]
```

Two contracts make this pipeline composable:

1. **JSON-file-only interface.** Skills never import each other's Python code. They communicate by reading and writing structured JSON in the run directory. This keeps each skill independently runnable and testable.
2. **Run-id directory convention.** A run is a directory `output/<YYYY-MM-DD_HHMM_slug>/` created by `paper-search`. Every downstream skill writes back into the same directory, so a finished run accumulates the full chain `query.json → raw_papers.json → ranked_papers.json → enriched_papers.json → graph.json + graph_metrics.json → briefing.{md,json}`.

A single `paper-search` call is the only thing that creates a run dir and updates `output/latest_run.txt`. Every other skill auto-discovers the latest run, so the common case is zero-config.

---

## Directory structure

```
arXiv-research-briefing-agent/
├── README.md                          # this file
├── AGENTS.md                          # AGENTS.md
│
├── .claude/
│   └── skills/
│       ├── paper-search/              # Skill 1 — arXiv retrieval
│       │   ├── SKILL.md
│       │   └── search.py
│       ├── paper-rank/                # Skill 2 — BM25 + recency ranking
│       │   ├── SKILL.md
│       │   └── rank.py
│       ├── paper-extract/             # Skill 3 — rule-based extraction
│       │   ├── SKILL.md
│       │   └── extract.py
│       ├── paper-network/             # Skill 4 — graph + metrics
│       │   ├── SKILL.md
│       │   └── network.py
│       ├── paper-report/              # Skill 5 — daily briefing
│       │   ├── SKILL.md
│       │   └── report.py
│       ├── follow-up/                 # Skill 6 — grounded Q&A
│       │   ├── SKILL.md
│       │   └── followup.py
│       │
│       ├── sch-create/                # vendor: StudyClawHub toolkit
│       ├── sch-deps/                  # vendor: dependency manager
│       ├── sch-install/               # vendor: install from registry
│       ├── sch-search/                # vendor: search registry
│       ├── sch-submit/                # vendor: publish to registry
│       ├── sch-delete/                # vendor: unregister
│       └── latex-report/              # vendor: NeurIPS LaTeX scaffolding
│
└── output/
    ├── latest_run.txt                 # run_id of the most recent search
    ├── cache/<16hex>.json             # arXiv response cache (shared)
    ├── query.json                     # staging slot for the next search
    └── 2026-05-06_2349_jepa/          # one run dir per paper-search call
        ├── query.json
        ├── raw_papers.json            # paper-search
        ├── ranked_papers.json         # paper-rank
        ├── enriched_papers.json       # paper-extract
        ├── graph.json                 # paper-network
        ├── graph_metrics.json         # paper-network
        ├── briefing.md                # paper-report
        ├── briefing.json              # paper-report
        └── followups.jsonl            # follow-up (when --save)
```

---

## Quick start

### Prerequisites

- Python 3.9+.
- Three PyPI packages: `arxiv >= 2.0` (for `paper-search`), `rank_bm25 >= 0.2` (for `paper-rank`), `networkx >= 3.0` (for `paper-network`). The other three skills (`paper-extract`, `paper-report`, `follow-up`) are stdlib-only.

```bash
# one-shot install
pip install "arxiv>=2.0" "rank_bm25>=0.2" "networkx>=3.0"
```

### Option A — natural language end-to-end (recommended)

Just type your research question in plain language inside Claude Code — no slash command needed:

```
为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习
```

or

```
find recent papers on diffusion models for medical imaging from the last 6 months
```

Claude (the agent) recognizes the intent, parses it into `output/query.json`, and chains the six skills automatically: `paper-search` → `paper-rank` → `paper-extract` → `paper-network` → `paper-report`. The briefing is then rendered back into the conversation, and you can ask follow-up questions in the same plain-language style:

```
详细讲讲 ...
对比 ... 和 ...
which papers are most novel?
```

This is the typical use mode — zero config, no scripts.

### Option B — per-skill slash commands (explicit control)

When you want to re-run a single stage (e.g. re-rank with different weights, or regenerate the briefing with a different `--top-n`), each skill has its own kebab-case slash trigger:

```
/paper-search 为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习
/paper-rank
/paper-extract
/paper-network
/paper-report
/follow-up tell me more about ...
```

Each skill auto-discovers the latest run via `output/latest_run.txt`, so you can stop and resume at any stage.

### Option C — run the scripts directly (outside Claude Code)

```bash
# 1. Author output/query.json yourself (or let Claude do it).
mkdir -p ./output
cat > ./output/query.json <<'EOF'
{
  "original_query": "find papers on JEPA from the last two years on CV and representation learning",
  "normalized_query": "JEPA representation learning computer vision",
  "search_terms": ["JEPA", "representation learning", "computer vision"],
  "start_date": "2024-05-06",
  "end_date": "2026-05-06",
  "categories": ["cs.CV", "cs.LG"],
  "max_results": 50
}
EOF

# 2. Search arXiv (creates a fresh run dir + updates latest_run.txt)
python .claude/skills/paper-search/search.py --query-file ./output/query.json

# 3. Re-rank (BM25 + recency)
python .claude/skills/paper-rank/rank.py

# 4. Extract structured info for the top-20
python .claude/skills/paper-extract/extract.py

# 5. Build paper / author / category / topic graph + metrics
python .claude/skills/paper-network/network.py

# 6. Render the daily briefing
python .claude/skills/paper-report/report.py

# 7. Ask follow-up questions
python .claude/skills/follow-up/followup.py "tell me more about ..."
python .claude/skills/follow-up/followup.py "compare ... and ..."
python .claude/skills/follow-up/followup.py "which papers are most novel?"
```

Every script supports `--input-dir <path>` to pin a specific run instead of using `latest_run.txt`.


---

## Skills 

| # | Skill | Reads | Writes | Stdlib-only? |
|---|---|---|---|---|
| 1 | `paper-search` | `output/query.json` | `<run>/raw_papers.json`, updates `latest_run.txt` | requires `arxiv` |
| 2 | `paper-rank` | `<run>/raw_papers.json` | `<run>/ranked_papers.json` | requires `rank_bm25` |
| 3 | `paper-extract` | `<run>/ranked_papers.json` | `<run>/enriched_papers.json` | yes |
| 4 | `paper-network` | `<run>/ranked_papers.json` (+ optional `enriched_papers.json`) | `<run>/graph.json`, `<run>/graph_metrics.json` | requires `networkx` |
| 5 | `paper-report` | `<run>/{ranked,enriched,graph_metrics}.json` | `<run>/briefing.md`, `<run>/briefing.json` | yes |
| 6 | `follow-up` | `<run>/briefing.json` (+ all earlier files) | answer to stdout, optional `<run>/followups.jsonl` | yes |

### 1. `paper-search` — data entry into arXiv

Translates the parsed `query.json` into an arXiv API call and writes deduplicated paper metadata. Uses the `arxiv` PyPI package with a SHA-256 query-keyed disk cache (`output/cache/`) for fast re-runs. The only skill that creates a run directory and updates `latest_run.txt`.

### 2. `paper-rank` — BM25 + recency ranking

Scores every paper for query relevance and recency, then combines them as `final_score = 0.80 * relevance_norm + 0.20 * recency` (weights are CLI-configurable). Relevance comes from `BM25Okapi` over `title + abstract + categories`; recency is min-max normalized publish date. Preserves all input fields and adds `rank` + `scores` per paper.

### 3. `paper-extract` — rule-based structured extraction

Pulls eight structured fields (`main_contribution`, `method`, `task`, `keywords`, `datasets_or_domains`, `evaluation_signals`, `limitations`, `evidence_sentences`) from each top-N paper's title + abstract using word-boundary regex cue matching. Pure stdlib, no LLM, no PDF parsing.

### 4. `paper-network` — graph analysis + metrics

Builds a heterogeneous `paper` / `author` / `category` / `topic` graph plus a paper-paper projection (default edge weights `3.0` / `1.5` / `1.0` for shared authors / categories / topics), then computes nine `graph_metrics` per paper: five centrality / novelty scores (`degree_centrality`, `betweenness_centrality`, `pagerank`, `bridging_score`, `novelty`) plus four feature counts. Implementation uses `networkx`.

### 5. `paper-report` — daily briefing

Joins `ranked_papers.json`, `enriched_papers.json`, and `graph_metrics.json` by canonical `id` and renders a Markdown briefing (top-N table + four-way highlights + per-paper notes) plus a structured `briefing.json` for `follow-up`. Pure stdlib.

### 6. `follow-up` — grounded Q&A over the cached run

Answers paper-detail, comparison, network-highlight, and keyword-search questions using only cached run JSON — no LLM, no arXiv calls. Cites paper IDs, ranks, URLs, and `evidence_sentences`; optional `--save` appends `{question, answer}` rows to `<run>/followups.jsonl`.

---

### Default I/O behavior every skill MUST implement

- If `--input-dir <path>` is passed, read inputs from there.
- Otherwise read `output/latest_run.txt` and use `output/<run_id>/`.
- Write outputs into the **same** directory.
- **Never** create a new run dir or modify `latest_run.txt` from a downstream skill — only `paper-search` does that.
- When `--output-dir` is passed explicitly to `paper-search`, it does NOT touch `latest_run.txt` (advanced / parallel-run usage).

This convention is what lets the agent be zero-config: one `paper-search` call seeds the chain; everything else just runs and finds the right files.

---

## Contributions

| Skills | Author |
|---|---|
|  `paper-search`,  `paper-rank`,  `paper-extract` | Yuzhe Zhuang |
|  `paper-network`,  `paper-report`,  `follow-up` | Ziyu Liang |

---

