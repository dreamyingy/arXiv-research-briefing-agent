---
name: paper-search
description: "Search arXiv for papers matching a natural-language research query and emit cleaned, deduplicated metadata as raw_papers.json. Trigger phrases: 'paper-search', 'search arxiv', 'find arxiv papers on X', '搜索arxiv论文', '运行 paper-search'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - search
  - research
  - briefing
---

# paper-search

Stage 1 of the daily arXiv briefing agent — the data entry. Parses a natural-language research query, calls the arXiv API, cleans the returned metadata, and writes `raw_papers.json` for downstream skills (`paper-rank`, `paper-network`). It does **not** rank, summarize, or analyze.

## Workflow

When the user invokes this skill (e.g. *"为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习"*):

### Step 1 — Parse the user's NL query into `query.json`

You (the agent) translate the user's request into the structure below. Translate Chinese / mixed terms into English keywords for `search_terms`. Apply defaults for any field the user did not mention.

```json
{
  "original_query": "为我寻找近两年有关 JEPA 的论文，重点关注计算机视觉和表征学习。",
  "normalized_query": "JEPA representation learning computer vision",
  "search_terms": ["JEPA", "representation learning", "computer vision"],
  "start_date": "2024-05-06",
  "end_date": "2026-05-06",
  "categories": ["cs.CV", "cs.LG"],
  "max_results": 50
}
```

**Defaults** (apply when the user did not specify):

| field | default |
|---|---|
| `start_date` | today − 30 days |
| `end_date` | today |
| `max_results` | 50 |
| `categories` | `[]` (no category filter) |

Write the JSON to `./output/query.json` (a stable staging slot). If the user already provides English keywords (e.g. *"search arxiv for JEPA"*), use them directly; do not over-translate.

### Step 2 — Run the search script

```
python search.py --query-file ./output/query.json
```

The script auto-creates a per-run directory `./output/<run_id>/` where `run_id = YYYY-MM-DD_HHMM_<slug>` (slug derived from the first 3 `search_terms`, slugified, ≤ 30 chars). It writes `raw_papers.json` and a copy of the resolved `query.json` into that dir, and overwrites `./output/latest_run.txt` with the new `run_id` so downstream skills can find it.

Flags:
- `--output-dir <path>` — override the auto-generated dir; when set, `latest_run.txt` is **not** updated (advanced / parallel-run usage).
- `--no-cache` — bypass cache and force a fresh arXiv request.

### Step 3 — Verify and report

- Read `./output/latest_run.txt` to get the `run_id`.
- Confirm `./output/<run_id>/raw_papers.json` exists.
- Read `count`. If `count == 0`, tell the user the query returned nothing and suggest broadening `search_terms`, the date range, or the category filter — do **not** silently proceed to downstream skills.
- Otherwise, report: `run_id`, number of papers, date range covered, and the path to `raw_papers.json`.

## Input: `query.json`

| field | type | required | notes |
|---|---|---|---|
| `original_query` | string | yes | raw user input, kept for traceability |
| `normalized_query` | string | yes | English keyword-normalized form |
| `search_terms` | string[] | yes | non-empty; ANDed in the arXiv query |
| `start_date` | `YYYY-MM-DD` | no | default = today − 30 days |
| `end_date` | `YYYY-MM-DD` | no | default = today |
| `categories` | string[] | no | arXiv categories, ORed (e.g. `cs.CV`, `cs.LG`) |
| `max_results` | int | no | default = 50 |

## Output: `raw_papers.json`

```json
{
  "query": { "...": "echo of the parsed query above" },
  "fetched_at": "2026-05-06T10:00:00+00:00",
  "count": 42,
  "papers": [
    {
      "id": "2401.12345",
      "version": "v2",
      "title": "Joint Embedding Predictive Architectures...",
      "abstract": "We propose ...",
      "authors": ["Alice Smith", "Bob Lee"],
      "primary_category": "cs.LG",
      "categories": ["cs.LG", "cs.CV"],
      "published": "2024-01-15",
      "updated": "2024-03-22",
      "url": "https://arxiv.org/abs/2401.12345",
      "pdf_url": "https://arxiv.org/pdf/2401.12345v2",
      "doi": null,
      "journal_ref": null,
      "comment": "12 pages"
    }
  ]
}
```

### Field naming convention (project-wide)

These names are the **canonical** per-paper field names. All downstream skills (`paper-rank`, `paper-extract`, `paper-network`, `paper-report`) MUST reuse them when reading or writing per-paper records:

- `id` — arXiv ID **without** version suffix (e.g. `2401.12345`)
- `version` — version suffix string (e.g. `v2`, possibly empty)
- `title`, `abstract` — strings, newlines collapsed to spaces
- `authors` — `string[]` of author names
- `primary_category` — single string from arXiv
- `categories` — `string[]`, includes `primary_category`
- `published`, `updated` — `YYYY-MM-DD` strings (date only, UTC)
- `url`, `pdf_url` — strings
- `doi`, `journal_ref`, `comment` — string or `null`

Top-level `count` always equals `len(papers)`. `query` echoes the parsed input for reproducibility.

## Example

```bash
mkdir -p ./output
cat > ./output/query.json <<'EOF'
{
  "original_query": "find me papers on JEPA from the last two years, focused on CV and representation learning",
  "normalized_query": "JEPA representation learning computer vision",
  "search_terms": ["JEPA"],
  "start_date": "2024-05-06",
  "end_date": "2026-05-06",
  "categories": ["cs.CV", "cs.LG"],
  "max_results": 50
}
EOF
python search.py --query-file ./output/query.json
# -> ./output/2026-05-06_1530_jepa/raw_papers.json
# -> ./output/latest_run.txt  contains "2026-05-06_1530_jepa"
```

The artifact for the next skill is `./output/<latest_run_id>/raw_papers.json`.

### Output layout

```
output/
├── latest_run.txt                    # contains the most recent run_id
├── cache/                            # shared cache, one file per query hash
│   └── <16hex>.json
├── query.json                        # staging slot (overwritten each invocation)
└── 2026-05-06_1530_jepa/             # per-run directory
    ├── query.json                    # resolved query (with defaults applied)
    └── raw_papers.json
```

## Downstream convention

All downstream skills (`paper-rank`, `paper-extract`, `paper-network`, `paper-report`) MUST follow the same pattern when consuming inputs:

1. If the user passes `--input-dir <path>`, read inputs from there.
2. Otherwise, read `./output/latest_run.txt` to discover the latest `run_id` and read inputs from `./output/<run_id>/`.
3. Write their own outputs into the same per-run directory (so a run dir accumulates `raw_papers.json` → `ranked_papers.json` → `enriched_papers.json` → `graph.json` → `briefing.*`).

This keeps the agent zero-config in the common case and parallelizable when a user pins specific run dirs.

## Caching

- Cache key = SHA-256 of the canonicalized query (`search_terms` and `categories` sorted), truncated to 16 hex chars.
- Cache files live at `./output/cache/<key>.json` (shared across runs in default mode). When `--output-dir` is set explicitly, cache lives at `<output-dir>/cache/<key>.json` instead (self-contained run).
- A cache hit copies the cached payload to `<output-dir>/raw_papers.json`, still creates a fresh per-run directory, and updates `latest_run.txt` — so a cached run looks identical to a fresh one to downstream skills.
- `--no-cache` bypasses this and always hits arXiv.

## Error handling

| condition | behavior |
|---|---|
| `query.json` not found | exit non-zero with the missing path |
| `search_terms` empty / missing | exit non-zero with a clear message |
| `start_date` / `end_date` not `YYYY-MM-DD` | exit non-zero, name the offending field |
| arXiv API failure (network / HTTP) | the `arxiv` client retries 3× with backoff; if all attempts fail, exit non-zero with the underlying error |
| Atom XML parse failure for one entry | log a `WARN` to stderr, skip the entry, continue with the rest |
| zero results returned | write `raw_papers.json` with `count: 0` and emit `WARN: zero results …`; exit 0 (not an error — the agent decides what to do next) |
| `arxiv` package missing | exit non-zero with the install hint (`pip install arxiv`) |

## Dependencies

- Python ≥ 3.9
- [`arxiv`](https://pypi.org/project/arxiv/) ≥ 2.0

## Independent test hooks (for course evaluation)

This skill can be evaluated in isolation:

- **count check** — for a known-good query (e.g. `JEPA`, `cs.LG`, last 12 months), `count > 0`.
- **dedup check** — no two entries in `papers` share the same `id`.
- **schema check** — every paper has all required fields; `published` / `updated` parse as `YYYY-MM-DD`; `authors` is a non-empty `string[]`.
- **cache check** — a second run with the same `query.json` is at least 3× faster than the first run and produces a `raw_papers.json` that matches the first run byte-for-byte.
- **empty-result handling** — a deliberately impossible query (e.g. `search_terms: ["zzzzzzzzzz"]`) writes `count: 0` without raising.
