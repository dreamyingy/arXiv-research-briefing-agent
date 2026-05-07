---
name: paper-extract
description: "From the top-N ranked papers, pull structured research info (main contribution, method, task, keywords, datasets/domains, evaluation signals, limitations, evidence sentences) out of title + abstract via lightweight rule-based extraction, writing enriched_papers.json. Trigger phrases: 'paper-extract', 'extract papers', 'pull contributions', '抽取论文', '运行 paper-extract'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - extraction
  - keywords
  - briefing
---

# paper-extract

Stage 3 of the daily arXiv briefing agent. Reads `ranked_papers.json` from the current run directory, takes the top-N papers, and pulls structured research signals out of their `title` + `abstract` using rule-based heuristics (no LLM call, no PDF download, no full-text parsing). Writes `enriched_papers.json` next to the input. It does **not** retrieve, rank, build graphs, or generate the final report.

## Workflow

### Step 1 — Locate the run directory

- If `--input-dir <path>` is given, use it directly.
- Otherwise, read `./output/latest_run.txt` to find the latest `run_id` and use `./output/<run_id>/`.

The directory must contain `ranked_papers.json`. If not, exit non-zero with a hint to run `paper-rank` first.

### Step 2 — Run the extract script

```
python extract.py
# or pin a run / change top-N:
python extract.py --input-dir ./output/2026-05-06_2349_jepa --top-n 20
```

The script reads `<input-dir>/ranked_papers.json`, processes the top-N papers (default 20), and writes `<input-dir>/enriched_papers.json` next to it. It does **not** create a new run directory and does **not** modify `latest_run.txt`.

Optional flags:
- `--top-n <int>` — default `20`. If `top_n > total papers`, all papers are processed.
- `--keyword-limit <int>` — default `8`. Max keywords per paper.

### Step 3 — Verify and report

- Confirm `<input-dir>/enriched_papers.json` exists.
- Confirm `count == min(top_n, ranked.count)`.
- Spot-check the top paper's `extraction.main_contribution` and `extraction.keywords`.

## Coordination with paper-search / paper-rank

`paper-extract` is a pure consumer of `paper-rank`'s output:

- **Reads** `<run_dir>/ranked_papers.json` (papers must already carry `rank` and `scores` from `paper-rank`).
- Uses `query.search_terms` from the top-level query to boost keyword scoring.
- **Writes** `<run_dir>/enriched_papers.json`.
- **Does not** touch `latest_run.txt`, the staging `query.json`, or `cache/`.

Downstream skills (`paper-network`, `paper-report`, `follow-up`) read `enriched_papers.json` from the same `<run_dir>`.

## Inputs

`<input-dir>/ranked_papers.json` (produced by `paper-rank`). The relevant fields per paper are: `title`, `abstract`, `categories`, `rank`. The query is taken from the top-level `query.search_terms`.

CLI flags:

| flag | type | default | notes |
|---|---|---|---|
| `--input-dir` | path | resolved from `latest_run.txt` | per-run directory; must contain `ranked_papers.json` |
| `--top-n` | int | `20` | how many top-ranked papers to extract from |
| `--keyword-limit` | int | `8` | max keywords per paper |

## Output: `enriched_papers.json`

Same shape as `ranked_papers.json`, with two additions:

1. Top-level `extracted_at` and `extraction_config` (echoes flags + method, for traceability).
2. Each paper gets an `extraction` field. All other fields (incl. `rank`, `scores`) are preserved verbatim.
3. The `papers` array contains only the top-N papers, in their existing rank order.

```json
{
  "query": { "...": "echo from upstream" },
  "fetched_at": "2026-05-06T15:49:44+00:00",
  "ranked_at": "2026-05-06T16:02:11+00:00",
  "ranking_config": { "...": "echo from paper-rank" },
  "extracted_at": "2026-05-06T16:30:00+00:00",
  "extraction_config": {
    "method": "rule_based_v1",
    "top_n": 20,
    "keyword_limit": 8,
    "source_fields": ["title", "abstract"]
  },
  "count": 20,
  "papers": [
    {
      "id": "2603.29966",
      "rank": 1,
      "scores": { "...": "from paper-rank" },
      "title": "...",
      "abstract": "...",
      "...": "all other fields from ranked_papers.json",
      "extraction": {
        "main_contribution": "We propose a JEPA-based framework that learns ...",
        "method": "Self-supervised pretraining with a latent predictor ...",
        "task": "representation learning for surgical video understanding",
        "keywords": ["jepa", "surgical video", "representation learning", "..."],
        "datasets_or_domains": ["EEG", "ImageNet", "surgical"],
        "evaluation_signals": [
          "outperforms prior baselines on benchmark X",
          "improves accuracy by 3.2%"
        ],
        "limitations": "However, the method is limited to short clips...",
        "evidence_sentences": {
          "contribution": "We propose a JEPA-based framework that learns ...",
          "method": "Self-supervised pretraining with a latent predictor ...",
          "task": "We address representation learning for surgical video ..."
        }
      }
    }
  ]
}
```

### Field naming convention

All per-paper fields from `ranked_papers.json` are preserved verbatim (project-wide canonical names). The new `extraction` block:

| field | type | notes |
|---|---|---|
| `main_contribution` | string | one-sentence summary of the paper's contribution |
| `method` | string | sentence describing the proposed method/framework |
| `task` | string | the task or application the paper addresses |
| `keywords` | string[] | up to `keyword_limit`, lowercased; may include unigrams and bigrams |
| `datasets_or_domains` | string[] | acronyms / dataset names / domain triggers detected in the abstract |
| `evaluation_signals` | string[] | sentence fragments containing evaluation cues (accuracy, benchmark, outperforms, …) |
| `limitations` | string | first sentence with a limitation cue (`however`, `limited`, `challenge`, …); empty string if none |
| `evidence_sentences` | object | original sentences picked for `contribution` / `method` / `task` (provenance for downstream report and follow-up) |

When a field cannot be extracted, the script returns the field's empty default (`""` for strings, `[]` for lists). `evidence_sentences.method` falls back to `evidence_sentences.contribution` when no method-specific sentence matched.

## Extraction methodology

Pure rule-based, stdlib only — no NLP libraries. Choices follow the spec in `create_paper_extract.txt`.

**Sentence splitter**: `re.split(r'(?<=[.!?])\s+(?=[A-Z])', text)`. Simple and predictable; occasionally splits on abbreviations like "et al.", which is acceptable for extraction quality at this scope.

**Trigger lists** (matched as **case-insensitive whole-word regex** against each sentence — `\bcue\b`, with internal spaces in multi-word cues compiled as `\s+`. This avoids false positives like `Models` matching the cue `model`.):

- *Contribution*: `we propose`, `we present`, `we introduce`, `we develop`, `we design`, `we show`, `this paper proposes`, `this work presents`, `we contribute`, `our work provides`. The first sentence containing any cue is selected.
- *Method*: `method`, `model`, `framework`, `architecture`, `pipeline`, `approach`, `algorithm`, `objective`, `pretraining`, `self-supervised`, `masked`, `latent`, `embedding`, `retrieval`, `diffusion`. First match, **skipping the title sentence** (the title is concatenated as the first sentence; method cues like `model` very often appear in titles and would shadow the real method description in the abstract). If still no match → fall back to the contribution sentence.
- *Task*: `task`, `classification`, `retrieval`, `segmentation`, `prediction`, `generation`, `representation learning`, `video understanding`, `time-series`, `inpainting`. First match (the title sentence is allowed because task words like `classification` legitimately appear in titles).
- *Limitations*: `however`, `limitation`, `limited`, `challenge`, `fail`, `failure`, `unclear`, `constrained`. First match (or `""`).
- *Evaluation signals*: `outperform`, `improve`, `achieve`, `accuracy`, `f1`, `auroc`, `auc`, `map`, `benchmark`, `state-of-the-art`, `baseline`, `evaluation`, `experiment`. All matching sentences, deduped, capped at 6.

**Keywords**: lowercase + `re.findall(r"[a-z0-9][a-z0-9-]*")`, drop a built-in stopword set (~80 common English words) and pure-digit / single-char tokens. Score each remaining unigram by frequency; multiply by 2 if it appears in the title; multiply by 3 if it appears (or one of its tokens does) in `query.search_terms`. Title bigrams (consecutive non-stopword tokens within the title) are added with a high prior so phrases like "joint embedding" survive single occurrences. Sort by score, keep `keyword_limit`.

**Datasets / domains**: union of two patterns—

- All-caps acronyms: `\b[A-Z][A-Z0-9]{1,7}\b` (catches `JEPA`, `EEG`, `AUROC`, `BEIR`).
- Mixed case dataset names: `\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b|\b[A-Z][a-zA-Z]*\d+[a-zA-Z]*\b` (catches `ImageNet`, `ETTh1`).
- Plus an explicit domain trigger list (`dataset`, `benchmark`, `corpus`, `cohort`, `domain`, `video`, `image`, `EEG`, `sonar`, `medical`, `surgical`, `audio`, `text`) — the ones that actually appear in the abstract are added. Deduped (case-insensitive), capped at 10.

## Example

```bash
# After paper-rank has populated the latest run
python extract.py
# pin a specific run, top-30, more keywords:
python extract.py --input-dir ./output/2026-05-06_2349_jepa --top-n 30 --keyword-limit 12
```

After running, `<input-dir>/enriched_papers.json` is the artifact for `paper-network` / `paper-report` / `follow-up`.

## Error handling

| condition | behavior |
|---|---|
| `--input-dir` not given AND `./output/latest_run.txt` missing | exit non-zero with hint: run `paper-search` then `paper-rank` first |
| `--input-dir` given but does not exist | exit non-zero, name the path |
| `ranked_papers.json` not found in input dir | exit non-zero with hint: run `paper-rank` first |
| `ranked_papers.json` malformed JSON / missing `papers` | exit non-zero with a clear message |
| `papers` is empty | write `enriched_papers.json` with `count: 0`, empty `papers`, emit `WARN: empty corpus`; exit 0 |
| `top_n > len(papers)` | process all available papers; not an error |
| individual extraction field has no match | return field's empty default (`""` or `[]`); per-paper extraction never raises |
| paper has empty `abstract` | extraction fields default to empty; emit `WARN: paper <id> has empty abstract` to stderr |
| `top_n <= 0` or `keyword_limit <= 0` | exit non-zero with a clear message |

## Dependencies

- Python ≥ 3.9
- **No third-party dependencies** — stdlib only.

## Independent test hooks (for course evaluation)

- **count check** — `enriched.count == min(top_n, ranked.count)`.
- **schema preservation** — every per-paper field present in `ranked_papers.json` is also present in `enriched_papers.json` with the same value.
- **extraction completeness** — every paper has an `extraction` block with the eight expected keys (`main_contribution`, `method`, `task`, `keywords`, `datasets_or_domains`, `evaluation_signals`, `limitations`, `evidence_sentences`); `evidence_sentences` always has the three keys `contribution` / `method` / `task`.
- **type check** — `keywords` / `datasets_or_domains` / `evaluation_signals` are lists of strings; the rest of `extraction` are strings (or sub-objects of strings).
- **stability** — running twice on the same `ranked_papers.json` produces a byte-identical `enriched_papers.json` (after stripping `extracted_at`).
- **fallback check** — for a paper whose abstract contains no method cues, `extraction.method == extraction.main_contribution` and `extraction.evidence_sentences.method == extraction.evidence_sentences.contribution`.
- **empty handling** — when `ranked_papers.json` has `papers: []`, the script writes `enriched_papers.json` with `papers: []` and exits 0.
