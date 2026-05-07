---
name: paper-network
description: "Build paper/author/category/topic networks from ranked_papers.json plus optional enriched_papers.json, compute graph metrics for every paper, and write graph.json and graph_metrics.json. Trigger phrases: 'paper-network', 'build paper graph', 'network analysis for papers', '论文网络分析', '运行 paper-network'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - network
  - graph
  - briefing
---

# paper-network

Stage 4 of the daily arXiv briefing agent. Reads the current run's `ranked_papers.json` and optional `enriched_papers.json`, builds a heterogeneous paper/author/category/topic graph plus a paper-paper projection graph, computes network metrics for every ranked paper, and writes `graph.json` and `graph_metrics.json` into the same run directory.

It does **not** search arXiv, rank papers, extract paper content, or generate the final report.

## Workflow

### Step 1 — Locate the run directory

- If `--input-dir <path>` is given, use it directly.
- Otherwise, read `./output/latest_run.txt` and use `./output/<run_id>/`.

The directory must contain `ranked_papers.json`. `enriched_papers.json` is optional; when present, its `extraction.keywords` and `extraction.datasets_or_domains` enrich topic nodes. Missing enriched data falls back to lightweight keywords from title, abstract, and categories.

### Step 2 — Run the network script

```bash
python network.py
# or pin a run:
python network.py --input-dir ./output/2026-05-07_1733_jepa-representation-learning-c
```

Optional flags:

| flag | type | default | notes |
|---|---|---|---|
| `--input-dir` | path | resolved from `latest_run.txt` | per-run directory containing `ranked_papers.json` |
| `--topic-limit` | int | `8` | max topic keywords attached to each paper |
| `--min-paper-edge-weight` | float | `1.0` | keep paper-paper projection edges with at least this shared-feature weight |
| `--max-feature-paper-fraction` | float | `0.70` | projection ignores features shared by more than this fraction of papers |
| `--no-topics` | bool | false | skip topic nodes and topic-based paper similarity |

### Step 3 — Verify and report

- Confirm `<input-dir>/graph.json` exists.
- Confirm `<input-dir>/graph_metrics.json` exists.
- Confirm `graph_metrics.count == ranked_papers.count`.
- Spot-check that every paper has `graph_metrics.degree_centrality`, `betweenness_centrality`, `pagerank`, `novelty`, and `bridging_score`.

## Coordination with upstream skills

`paper-network` is a pure JSON consumer:

- **Reads** `<run_dir>/ranked_papers.json` for the full paper corpus.
- **Optionally reads** `<run_dir>/enriched_papers.json` for top-N extracted keywords/domains.
- **Writes** `<run_dir>/graph.json` and `<run_dir>/graph_metrics.json`.
- **Does not** create a new run directory and **does not** modify `latest_run.txt`.

Downstream `paper-report` should join graph metrics by the canonical per-paper `id`.

## Output: `graph.json`

```json
{
  "query": { "...": "echo from ranked_papers.json" },
  "fetched_at": "...",
  "ranked_at": "...",
  "networked_at": "2026-05-07T10:00:00+00:00",
  "network_config": {
    "method": "heterogeneous_graph_plus_paper_projection_v1",
    "topic_limit": 8,
    "include_topics": true,
    "min_paper_edge_weight": 1.0,
    "max_feature_paper_fraction": 0.7
  },
  "count": 21,
  "graph_summary": {
    "node_count": 120,
    "edge_count": 260,
    "paper_projection_node_count": 21,
    "paper_projection_edge_count": 73
  },
  "nodes": [
    {
      "node_id": "paper:2502.18056",
      "type": "paper",
      "label": "Escaping The Big Data Paradigm...",
      "id": "2502.18056",
      "rank": 1
    }
  ],
  "edges": [
    {
      "source": "paper:2502.18056",
      "target": "author:alice-smith",
      "type": "paper-author",
      "weight": 1.0
    }
  ],
  "paper_projection_edges": [
    {
      "source": "2502.18056",
      "target": "2604.10591",
      "weight": 2.5,
      "shared_features": ["category:cs.CV", "topic:jepa"]
    }
  ]
}
```

## Output: `graph_metrics.json`

Same row-per-paper shape as `ranked_papers.json`, preserving all ranked paper fields and adding `graph_metrics` to each paper.

```json
{
  "query": { "...": "echo from ranked_papers.json" },
  "fetched_at": "...",
  "ranked_at": "...",
  "ranking_config": { "...": "echo from paper-rank" },
  "networked_at": "2026-05-07T10:00:00+00:00",
  "network_config": { "...": "same config as graph.json" },
  "count": 21,
  "papers": [
    {
      "id": "2502.18056",
      "rank": 1,
      "scores": { "final_score": 0.91 },
      "...": "all other ranked_papers.json fields",
      "graph_metrics": {
        "degree_centrality": 0.42,
        "betweenness_centrality": 0.08,
        "pagerank": 0.06,
        "weighted_degree": 7.5,
        "novelty": 0.25,
        "bridging_score": 0.08,
        "author_count": 4,
        "topic_count": 8,
        "category_count": 2
      }
    }
  ]
}
```

## Method

The script builds two related graphs:

1. **Heterogeneous graph** for visualization and inspection:
   - paper nodes from every row in `ranked_papers.json`
   - author nodes from `authors`
   - category nodes from `categories`
   - topic nodes from `enriched_papers.extraction.keywords`, `datasets_or_domains`, or fallback keywords
2. **Paper projection graph** for metrics:
   - one node per paper
   - an edge connects two papers that share authors, categories, or topics
   - default weights: shared author `3.0`, shared category `1.5`, shared topic `1.0`
   - very common features are ignored for projection edges by default, so a broad category shared by nearly every result does not make the paper graph fully connected

Metrics are computed on the paper projection graph:

- `degree_centrality`: NetworkX degree centrality on paper-paper edges.
- `betweenness_centrality`: NetworkX weighted betweenness centrality.
- `pagerank`: NetworkX PageRank using edge weights.
- `weighted_degree`: sum of paper-paper edge weights.
- `bridging_score`: same value as betweenness centrality, exposed under a report-friendly name.
- `novelty`: `1 - normalized(weighted_degree)`, so papers sharing fewer features with the corpus score as more novel.

## Error handling

| condition | behavior |
|---|---|
| `--input-dir` not given AND `./output/latest_run.txt` missing | exit non-zero with hint to run upstream skills first |
| `--input-dir` given but not a directory | exit non-zero, name the path |
| `ranked_papers.json` missing | exit non-zero with hint to run `paper-rank` first |
| `ranked_papers.json` malformed / missing `papers` | exit non-zero with a clear message |
| `enriched_papers.json` missing | emit `WARN`, fall back to title/abstract/category topics |
| `papers` empty | write empty `graph.json` and `graph_metrics.json`, emit `WARN`, exit 0 |
| `networkx` missing | exit non-zero with install hint (`pip install networkx`) |
| paper missing authors/categories/abstract | use empty defaults and continue |

## Dependencies

- Python ≥ 3.9
- `networkx`

## Independent test hooks

- **count preservation** — `graph_metrics.count == ranked_papers.count`.
- **schema preservation** — every field in each ranked paper is preserved unchanged; only `graph_metrics` is added.
- **join check** — every paper node in `graph.json` has canonical `id` matching a row in `graph_metrics.json`.
- **metric range check** — `degree_centrality`, `betweenness_centrality`, `pagerank`, `novelty`, and `bridging_score` are non-negative; normalized metrics are in `[0, 1]`.
- **empty handling** — empty `ranked_papers.json` writes valid empty outputs and exits 0.
