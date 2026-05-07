---
name: paper-report
description: "Generate a structured daily arXiv briefing from ranked_papers.json, enriched_papers.json, and graph_metrics.json, writing briefing.md and briefing.json. Trigger phrases: 'paper-report', 'generate briefing', 'make arxiv report', '生成论文日报', '运行 paper-report'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - report
  - briefing
  - research
---

# paper-report

Stage 5 of the daily arXiv briefing agent. Reads the current run's `ranked_papers.json`, `enriched_papers.json`, and `graph_metrics.json`, joins them by canonical paper `id`, and writes a human-readable `briefing.md` plus machine-readable `briefing.json` into the same run directory.

It does **not** search arXiv, rank papers, extract contributions, compute network metrics, or answer follow-up questions.

## Workflow

### Step 1 — Locate the run directory

- If `--input-dir <path>` is given, use it directly.
- Otherwise, read `./output/latest_run.txt` and use `./output/<run_id>/`.

The directory must contain `ranked_papers.json`, `enriched_papers.json`, and `graph_metrics.json`. `graph.json` is optional and used only for graph-level summary counts when present.

### Step 2 — Run the report script

```bash
python report.py
# or pin a run:
python report.py --input-dir ./output/2026-05-07_1733_jepa-representation-learning-c --top-n 10
```

Optional flags:

| flag | type | default | notes |
|---|---|---|---|
| `--input-dir` | path | resolved from `latest_run.txt` | per-run directory |
| `--top-n` | int | `10` | number of ranked papers to show in the briefing |
| `--no-graph-metrics` | bool | false | omit graph metrics from report rendering |

### Step 3 — Verify

- Confirm `<input-dir>/briefing.md` exists.
- Confirm `<input-dir>/briefing.json` exists.
- Confirm `briefing.json["count"] == min(top_n, ranked_papers.count)`.
- Confirm every reported paper has a title, rank, URL, score block, extraction block, and optional graph metrics block.

## Inputs

- `<run_dir>/ranked_papers.json` — full ranked corpus from `paper-rank`.
- `<run_dir>/enriched_papers.json` — extracted info for top-N papers from `paper-extract`.
- `<run_dir>/graph_metrics.json` — graph metrics for every ranked paper from `paper-network`.
- `<run_dir>/graph.json` — optional graph summary.

## Outputs

### `briefing.md`

Markdown report with:

1. Query and corpus summary.
2. Top papers table: rank, title, authors, published date, category, final score, PageRank, novelty, URL.
3. Highlights: most relevant, most novel, most bridging, highest PageRank.
4. Per-paper notes: contribution, method, task, keywords, datasets/domains, evaluation signals, limitations, and evidence sentences.
5. Graph summary when `graph.json` is available.

### `briefing.json`

Structured version of the same content:

```json
{
  "query": { "...": "echo from ranked_papers.json" },
  "generated_at": "2026-05-07T10:00:00+00:00",
  "report_config": { "top_n": 10, "include_graph_metrics": true },
  "source_files": {
    "ranked_papers": "ranked_papers.json",
    "enriched_papers": "enriched_papers.json",
    "graph_metrics": "graph_metrics.json",
    "graph": "graph.json"
  },
  "counts": {
    "ranked_papers": 21,
    "enriched_papers": 20,
    "graph_metric_papers": 21,
    "reported_papers": 10
  },
  "highlights": {
    "most_relevant": { "id": "...", "rank": 1, "title": "...", "reason": "..." },
    "most_novel": { "...": "..." },
    "most_bridging": { "...": "..." },
    "highest_pagerank": { "...": "..." }
  },
  "graph_summary": { "...": "from graph.json when present" },
  "count": 10,
  "papers": [
    {
      "id": "2502.18056",
      "rank": 1,
      "title": "...",
      "authors": ["..."],
      "url": "...",
      "scores": { "...": "from ranked_papers.json" },
      "extraction": { "...": "from enriched_papers.json when present" },
      "graph_metrics": { "...": "from graph_metrics.json" }
    }
  ]
}
```

## Error handling

| condition | behavior |
|---|---|
| `latest_run.txt` missing and `--input-dir` not passed | exit non-zero with hint to run upstream skills |
| required input JSON missing | exit non-zero, name the missing file |
| malformed JSON | exit non-zero with parser message |
| `ranked_papers.json` missing `papers` | exit non-zero |
| `enriched_papers.json` missing some top-N papers | continue with empty extraction defaults and emit `WARN` |
| `graph_metrics.json` missing some top-N papers | continue with empty metric defaults and emit `WARN` |
| ranked corpus empty | write valid empty `briefing.md` and `briefing.json`, emit `WARN`, exit 0 |
| `--top-n <= 0` | exit non-zero |

## Dependencies

- Python ≥ 3.9
- No third-party dependencies.

## Independent test hooks

- **count check** — `briefing.count == min(top_n, ranked.count)`.
- **join check** — all `papers[].id` values come from `ranked_papers.json`.
- **coverage check** — every reported paper has `extraction` and `graph_metrics` keys, even if one input is partially missing.
- **markdown check** — `briefing.md` contains the query, top paper table, highlights, and one section per reported paper.
