---
name: arxiv-research-briefing-agent
description: "Daily arXiv research briefing agent. Given a natural-language research query (English or Chinese), searches arXiv, ranks results, extracts structured research info, builds a paper/author/topic graph, produces a Markdown + JSON briefing, and answers grounded follow-up questions. Six independently-testable skills wired together by JSON files in a per-run directory."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - briefing
  - research
  - agent
  - bm25
  - network
skills:
  - paper-search
  - paper-rank
  - paper-extract
  - paper-network
  - paper-report
  - follow-up
---

# arXiv Research Briefing Agent

A six-skill agent that turns a natural-language research query into a daily arXiv briefing plus grounded follow-up Q&A. Skills communicate only through JSON files in a per-run directory `output/<run_id>/`, so any single skill can be re-run, swapped, or evaluated in isolation.

The deterministic post-search stages (`paper-extract`, `paper-network`, `paper-report`, `follow-up`) do **not** call an LLM — they are rule-based and stdlib-driven for reproducibility, which is also what makes the rubric's stability check tractable.

## Skills

| # | Skill | Reads | Writes |
|---|---|---|---|
| 1 | `paper-search` | `output/query.json` | `<run>/raw_papers.json` (+ updates `latest_run.txt`) |
| 2 | `paper-rank` | `<run>/raw_papers.json` | `<run>/ranked_papers.json` |
| 3 | `paper-extract` | `<run>/ranked_papers.json` | `<run>/enriched_papers.json` |
| 4 | `paper-network` | `<run>/ranked_papers.json` (+ optional `enriched_papers.json`) | `<run>/graph.json`, `<run>/graph_metrics.json` |
| 5 | `paper-report` | `<run>/{ranked,enriched,graph_metrics}.json` | `<run>/briefing.md`, `<run>/briefing.json` |
| 6 | `follow-up` | `<run>/briefing.json` (+ all earlier JSON) | answer to stdout (+ optional `<run>/followups.jsonl`) |

Each skill ships in `.claude/skills/<name>/` with its own `SKILL.md` (I/O contract, error-handling table, independent test hooks) and a single Python entry point.

## Workflow

```
paper-search ─→ raw_papers.json ─→ paper-rank ─→ ranked_papers.json ─┬─→ paper-extract ─→ enriched_papers.json ─┐
                                                                     │                                          │
                                                                     └─→ paper-network ─→ graph.json            │
                                                                              ▲           graph_metrics.json    │
                                                                              │                                 │
                                                                              └── optional input ◄──────────────┘

ranked_papers.json + enriched_papers.json + graph_metrics.json ─→ paper-report ─→ briefing.{md,json} ─→ follow-up
```

`paper-extract` and `paper-network` both consume `ranked_papers.json` and can run in parallel; `paper-network` additionally accepts `enriched_papers.json` as an optional input for richer topic nodes.

## Composition contracts

Two contracts make the pipeline composable:

1. **JSON-file-only interface.** Skills never import each other's Python code. They communicate by reading and writing structured JSON in the run directory.
2. **Run-id directory convention.** A run is a directory `output/<YYYY-MM-DD_HHMM_slug>/` created by `paper-search`. Every downstream skill writes back into the same directory, so a finished run accumulates the full chain `query.json → raw_papers.json → ranked_papers.json → enriched_papers.json → graph.json + graph_metrics.json → briefing.{md,json}`.

A single `paper-search` call is the only thing that creates a run dir and updates `output/latest_run.txt`. Every other skill auto-discovers the latest run via `latest_run.txt`, or accepts `--input-dir <path>` to pin a specific run.

## End-to-end invocation

### Natural language (recommended)

Inside Claude Code, just type the research question in plain language — no slash command:

```
为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习
```

Claude (the agent) recognizes the intent, parses it into `output/query.json`, and chains all six skills automatically. The briefing is rendered back into the conversation, and follow-up questions (*"详细讲讲 rank 1"*, *"compare rank 1 and rank 3"*, *"which papers are most novel?"*) are answered the same way. This is the typical use mode.

### Per-skill slash commands (explicit control)

Use slash commands when you want to re-run a single stage (e.g. re-rank with different weights, regenerate the briefing with a different `--top-n`):

```
/paper-search 为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习
/paper-rank
/paper-extract
/paper-network
/paper-report
/follow-up tell me more about rank 1
```

Each skill auto-discovers the latest run via `output/latest_run.txt`.

### Direct script invocation (outside Claude Code)

```bash
python .claude/skills/paper-search/search.py --query-file ./output/query.json
python .claude/skills/paper-rank/rank.py
python .claude/skills/paper-extract/extract.py
python .claude/skills/paper-network/network.py
python .claude/skills/paper-report/report.py
python .claude/skills/follow-up/followup.py "tell me more about rank 1"
```

Every script accepts `--input-dir <path>` to pin a specific run.

## Inputs and outputs at the agent level

**Input** — `output/query.json` (Claude-written from the user's NL request):

| field | type | required |
|---|---|---|
| `original_query` | string | yes |
| `normalized_query` | string | yes |
| `search_terms` | string[] | yes |
| `start_date` / `end_date` | `YYYY-MM-DD` | no (defaults: today − 30 days, today) |
| `categories` | string[] | no (default: `[]`) |
| `max_results` | int | no (default: `50`) |

**Output** — a per-run directory `output/<YYYY-MM-DD_HHMM_slug>/` containing the full chain `query.json → raw_papers.json → ranked_papers.json → enriched_papers.json → graph.json + graph_metrics.json → briefing.{md,json}`, plus `followups.jsonl` when `follow-up --save` is used.

The user-facing artifact is `<run>/briefing.md`; `<run>/briefing.json` is the structured form that `follow-up` consumes.

## Dependencies

- Python ≥ 3.9.
- Three PyPI packages: `arxiv >= 2.0` (`paper-search`), `rank_bm25 >= 0.2` (`paper-rank`), `networkx >= 3.0` (`paper-network`). The other three skills are stdlib-only.

```bash
pip install "arxiv>=2.0" "rank_bm25>=0.2" "networkx>=3.0"
```

## References

- `.claude/skills/<name>/SKILL.md` — authoritative per-skill I/O contract, error-handling table, and independent test hooks.
- `CLAUDE.md` — repo-level guidance for Claude Code: project conventions, canonical per-paper schema, gotchas.
- `README.md` — public-facing project overview, directory structure, quick-start.
- `FinalProjectGuidance.pdf` — assignment rubric (skill independence, quantifiable evaluation requirements).
- StudyClawHub registry: <https://trust-app-ai-lab.github.io/StudyClawHub/>.
