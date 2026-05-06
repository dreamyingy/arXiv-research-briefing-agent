---
name: paper-rank
description: "Re-rank arXiv papers from raw_papers.json by relevance (BM25 over title + abstract + categories vs the user query) and recency (date-normalized within the corpus), writing ranked_papers.json. Trigger phrases: 'paper-rank', 'rank papers', 'sort papers by relevance', '排序论文', '运行 paper-rank'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - ranking
  - bm25
  - briefing
---

# paper-rank

Stage 2 of the daily arXiv briefing agent. Reads `raw_papers.json` produced by `paper-search`, scores each paper for query relevance (BM25) and recency, combines them into a `final_score`, and writes `ranked_papers.json` into the **same per-run directory**. It does **not** retrieve, summarize, or analyze graphs — and it does **not** truncate to top-N (that is `paper-extract`'s job).

## Workflow

### Step 1 — Locate the run directory

- If the user passed `--input-dir <path>`, use that directory.
- Otherwise, read `./output/latest_run.txt` to discover the latest `run_id` and use `./output/<run_id>/`.

If neither resolves to a directory containing `raw_papers.json`, exit non-zero with a clear hint to run `paper-search` first.

### Step 2 — Run the rank script

```
python rank.py
# or pin a run:
python rank.py --input-dir ./output/2026-05-06_2349_jepa
```

The script reads `<input-dir>/raw_papers.json`, computes scores, and writes `<input-dir>/ranked_papers.json` next to it. It does **not** create a new run directory and does **not** modify `latest_run.txt`.

Optional flags:
- `--relevance-weight <float>` — default `0.80`
- `--recency-weight <float>` — default `0.20`

### Step 3 — Verify and report

- Confirm `<input-dir>/ranked_papers.json` exists.
- Read `count`. It must equal the `count` in `raw_papers.json` (no truncation).
- Report the top 5 by `final_score` and the path to `ranked_papers.json`.

## Coordination with paper-search

`paper-rank` is a pure consumer of `paper-search`'s output. It:

- **Reads** `<run_dir>/raw_papers.json` (and uses `query` field within it as the relevance query — does not re-read `query.json`).
- **Writes** `<run_dir>/ranked_papers.json`.
- **Does not** touch `./output/latest_run.txt`, the staging `./output/query.json`, or the `./output/cache/`.

Downstream skills (`paper-extract`, `paper-network`, `paper-report`) read `ranked_papers.json` from the same `<run_dir>`.

## Inputs

`<input-dir>/raw_papers.json` (produced by `paper-search`). The relevant fields per paper are: `title`, `abstract`, `categories`, `published`. The query is taken from the top-level `query` object, specifically `normalized_query` and `search_terms`.

CLI flags:

| flag | type | default | notes |
|---|---|---|---|
| `--input-dir` | path | resolved from `latest_run.txt` | per-run directory; must contain `raw_papers.json` |
| `--relevance-weight` | float | `0.80` | weight on `relevance_score_normalized` in `final_score` |
| `--recency-weight` | float | `0.20` | weight on `recency_score` in `final_score` |

The two weights do **not** need to sum to 1 — the score is a linear combination. Defaults sum to 1 so `final_score ∈ [0, 1]`.

## Output: `ranked_papers.json`

Same shape as `raw_papers.json`, with three additions:

1. Top-level `ranked_at` (UTC ISO timestamp) and `ranking_config` (echoes weights and method, for ablation traceability).
2. Each paper gets a `rank` field (1-indexed, by `final_score` descending).
3. Each paper gets a `scores` field with the four numbers used to rank it.
4. The `papers` array is reordered by `final_score` descending; ties preserve the original (SubmittedDate-descending) order.

```json
{
  "query": { "...": "echo from raw_papers.json (unchanged)" },
  "fetched_at": "2026-05-06T15:49:44+00:00",
  "ranked_at": "2026-05-06T16:02:11+00:00",
  "ranking_config": {
    "relevance_method": "bm25_okapi",
    "relevance_weight": 0.80,
    "recency_weight": 0.20,
    "doc_fields": ["title", "abstract", "categories"],
    "query_fields": ["normalized_query", "search_terms"]
  },
  "count": 50,
  "papers": [
    {
      "id": "2605.03245",
      "version": "v1",
      "title": "...",
      "abstract": "...",
      "authors": ["..."],
      "primary_category": "cs.LG",
      "categories": ["cs.LG", "cs.CV"],
      "published": "2026-05-05",
      "updated": "2026-05-05",
      "url": "https://arxiv.org/abs/2605.03245",
      "pdf_url": "...",
      "doi": null,
      "journal_ref": null,
      "comment": null,
      "rank": 1,
      "scores": {
        "relevance_score": 14.27,
        "relevance_score_normalized": 1.0,
        "recency_score": 1.0,
        "final_score": 1.0
      }
    }
  ]
}
```

### Field naming convention

All per-paper fields from `raw_papers.json` are preserved verbatim (project-wide canonical names — see `paper-search/SKILL.md`). The two new fields are:

- `rank` — int, 1-indexed, dense (1..N)
- `scores` — object:
  - `relevance_score` — raw BM25 score (≥ 0, unbounded)
  - `relevance_score_normalized` — min-max normalized over this corpus, ∈ [0, 1]
  - `recency_score` — `(published - min_published) / (max_published - min_published)`, ∈ [0, 1]
  - `final_score` — `relevance_weight * relevance_score_normalized + recency_weight * recency_score`

## Scoring methodology

**Relevance — BM25Okapi over (title + abstract + categories)**

- Tokenizer: lowercase, then `re.findall(r"[a-z0-9]+")` (drops punctuation, keeps alphanumerics).
- Document = `title + " " + abstract + " " + " ".join(categories)`, tokenized.
- Query = tokenize(`normalized_query`) ∪ tokenize(" ".join(`search_terms`)), de-duplicated while preserving order.
- Implementation: [`rank_bm25`](https://pypi.org/project/rank-bm25/)'s `BM25Okapi` with default `k1=1.5, b=0.75`.
- Min-max normalize raw BM25 scores across the corpus to `[0, 1]`. If `max == min` (degenerate corpus), fall back to `0.5` for every paper.

**Recency — date min-max within the corpus**

- `recency_score = (paper_date - min_date) / (max_date - min_date)`, where dates are parsed from `published` (`YYYY-MM-DD`).
- If all papers share the same `published` date (degenerate), fall back to `0.5` for every paper.

**Final score**

- `final_score = relevance_weight * relevance_score_normalized + recency_weight * recency_score`
- Defaults: `0.80 * relevance_score_normalized + 0.20 * recency_score`. The user can override via flags for ablation studies.

**Why these choices**

- BM25 over title+abstract is the standard text-relevance baseline — well-suited to ~50–500 paper corpora and zero training cost.
- Including `categories` rewards papers in the user's targeted arXiv areas (e.g. `cs.CV`, `cs.LG`).
- Min-max normalizing relevance puts both signals on the same `[0, 1]` scale so the weights mean what they look like.
- 0.80 / 0.20 default favors topical match over freshness — appropriate for "find me papers about X", not for a daily "what's new" feed.

## Example

```bash
# After paper-search has populated the latest run
python rank.py
# or pin a specific run:
python rank.py --input-dir ./output/2026-05-06_2349_jepa
# ablation: pure relevance
python rank.py --relevance-weight 1.0 --recency-weight 0.0
```

After running, `<input-dir>/ranked_papers.json` is the artifact for `paper-extract`.

## Error handling

| condition | behavior |
|---|---|
| `--input-dir` not given AND `./output/latest_run.txt` missing | exit non-zero with hint: run `paper-search` first |
| `--input-dir` given but does not exist or is not a dir | exit non-zero, name the path |
| `raw_papers.json` not found in input dir | exit non-zero, name the missing path |
| `raw_papers.json` malformed JSON / missing `query` field | exit non-zero with a clear message |
| `papers` is empty | write `ranked_papers.json` with `count: 0`, empty `papers`, emit `WARN: empty corpus`; exit 0 |
| corpus of size 1 / all identical scores or dates | normalized scores fall back to `0.5`; runs cleanly without divide-by-zero |
| `rank_bm25` package missing | exit non-zero with install hint (`pip install rank_bm25`) |

## Dependencies

- Python ≥ 3.9
- [`rank_bm25`](https://pypi.org/project/rank-bm25/) ≥ 0.2

Run `/sch-deps` to install or verify.

## Independent test hooks (for course evaluation)

- **count preservation** — `len(ranked.papers) == raw.count` (no truncation, by design).
- **rank check** — `papers[i]["rank"] == i + 1` for all `i`; ranks are dense `1..N`.
- **sort check** — `papers` is non-increasing in `scores.final_score`.
- **range check** — for every paper, `relevance_score_normalized`, `recency_score`, `final_score` all lie in `[0, 1]`; `relevance_score >= 0`.
- **schema preservation** — every per-paper field present in `raw_papers.json` is also present in `ranked_papers.json` with the same value.
- **stability** — running twice on the same `raw_papers.json` produces a byte-identical `ranked_papers.json` (after stripping `ranked_at`).
- **weight ablation** — `--relevance-weight 1.0 --recency-weight 0.0` yields a ranking that ignores recency (verifiable by sorting raw papers by BM25 alone and comparing).
- **empty handling** — when `raw_papers.json` has `papers: []`, the script writes `ranked_papers.json` with `papers: []` and exits 0.
