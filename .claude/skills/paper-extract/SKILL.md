---
name: paper-extract
description: "From the top-N ranked papers, pull structured research info (main contribution, method, task, keywords, datasets/domains, evaluation signals, limitations, evidence sentences) out of title + abstract via rule-based extraction, then run an agent-review pass that enforces every output is verbatim-grounded in the paper. Writes enriched_papers.json. Trigger phrases: 'paper-extract', 'extract papers', 'pull contributions', '抽取论文', '运行 paper-extract', 'review extraction', '审阅 enriched', '复核 extraction'."
author: dreamyingy
version: 2.0.0
tags:
  - arxiv
  - extraction
  - keywords
  - briefing
  - agent-review
---

# paper-extract

Stage 3 of the daily arXiv briefing agent. Reads `ranked_papers.json` from the current run directory, takes the top-N papers, pulls structured research signals out of their `title` + `abstract` using rule-based heuristics (stdlib only, no LLM call, no PDF download, no full-text parsing), and writes `enriched_papers.json` next to the input.

**The default invocation is two steps**: (1) `extract.py` runs the deterministic rule-based extractor, (2) the Claude Code agent reviews the output and revises any field that fails the faithfulness contract, writing back to the same file. The Python script itself never calls an LLM and remains byte-stable across reruns; the agent-review pass is performed at the orchestration layer by Claude Code, so no API key or network call is required from the script.

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

### Step 3 — Agent review pass (default, automatic)

As soon as `extract.py` finishes, Claude Code **must** perform the agent-review pass on `<input-dir>/enriched_papers.json` and write the revised file back to the same path, **before** returning control to the user and **before** any downstream skill (`paper-network` / `paper-report` / `follow-up`) is invoked. The agent does not wait for the user to ask, does not ask for confirmation, and does not skip the pass. It is a non-optional stage of `paper-extract`. Natural-language phrasings such as *"review extraction"* / *"审阅 enriched"* / *"复核 extraction"* are only relevant for **re-running** the pass on an already-reviewed file.

**Step 3.0 — Backup.** Before any edit, copy `<input-dir>/enriched_papers.json` to `<input-dir>/enriched_papers.rule_based.json` (skip if it already exists from a prior review). This preserves the deterministic rule-based output so rule-vs-reviewed diffs are always possible.

**Step 3.1 — Read inputs.** Read these two files from `<input-dir>`:
- `ranked_papers.json` → use `query.search_terms` and each paper's `title` / `abstract` as the **only** sources of ground truth.
- `enriched_papers.json` → the rule-based extraction to review.

**Step 3.2 — Apply the hard-constraint checklist per paper.** Every field below must satisfy the listed rule. If it does not, modify the field as described.

| field | hard constraint | action when violated |
|---|---|---|
| `main_contribution` | must be a sentence appearing verbatim (whitespace-collapsed) in `title + abstract`; must actually state this paper's contribution, not background context | replace with the abstract sentence that states the contribution; if the abstract has no contribution statement, set to `""` |
| `method` | verbatim sentence in `title + abstract`; must describe the proposed method/model/algorithm/architecture, not the task or background | replace with the method-describing sentence; **must not** be identical to `main_contribution` unless the abstract has exactly one sentence usable for both |
| `task` | verbatim sentence in `title + abstract`; must describe the task / application / problem setting | replace; set to `""` if none |
| `keywords[]` | each must match `\b{kw}\b` (case-insensitive) in `title + abstract`; no commonsense additions | delete unmatched items; **do not** add new keywords not present in the text |
| `datasets_or_domains[]` | each must word-boundary match in `title + abstract`; must actually denote a dataset, benchmark, or research domain; **must not** be in the generic blacklist (`AI ML DL NLP CV NN GPU TPU CNN RNN DNN MLP LSTM SOTA ICLR NEURIPS CVPR ECCV ICCV ACL EMNLP`) | delete unmatched, generic, or non-dataset items |
| `evaluation_signals[]` | each is a verbatim sentence from `title + abstract`; must actually mention experiments / metrics / comparisons | delete unfit sentences |
| `limitations` | verbatim sentence in `title + abstract`; **must explicitly state** a limitation / failure mode / open challenge of the paper's own method. A discourse "However, we propose..." does **not** count | set to `""` when the abstract states no real limitation |
| `evidence_sentences.{contribution, method, task}` | each is a verbatim sentence in `title + abstract`; should corroborate the corresponding claim field | replace with a corroborating second sentence when one exists; otherwise mirror the claim field |

**Step 3.3 — Non-negotiable boundaries.** The agent:

- modifies **only** the `extraction` block of each paper;
- never touches `id`, `version`, `title`, `abstract`, `authors`, `categories`, `primary_category`, `published`, `updated`, `url`, `pdf_url`, `doi`, `journal_ref`, `comment`, `rank`, or `scores`;
- never modifies top-level `query` / `count` / `fetched_at` / `ranked_at` / `extracted_at` / `ranking_config`;
- never adds new fields, never removes fields, never changes a field's type (`""` not `null`, `[]` not `null`);
- **never** uses outside knowledge or commonsense to backfill information that is not literally in `title + abstract`.

**Step 3.4 — Record the review.** Extend `extraction_config` (top-level, not per-paper) with a `review` sub-block:

```json
"extraction_config": {
  "method": "agent_reviewed_v1",
  "top_n": 20,
  "keyword_limit": 8,
  "source_fields": ["title", "abstract"],
  "review": {
    "reviewed_at": "2026-05-12T10:30:00+00:00",
    "reviewer": "claude-code-agent",
    "papers_reviewed": 20,
    "papers_modified": 7,
    "fields_modified": {
      "main_contribution": 1,
      "method": 3,
      "task": 0,
      "keywords": 2,
      "datasets_or_domains": 4,
      "evaluation_signals": 0,
      "limitations": 5,
      "evidence_sentences": 1
    }
  }
}
```

Bump `extraction_config.method` from `"rule_based_v2"` to `"agent_reviewed_v1"` after the review pass. No downstream skill reads `extraction_config`, so this is purely traceability metadata.

**Step 3.5 — Write back.** Save with `encoding="utf-8"`, `ensure_ascii=False`, `indent=2`, matching the script's format.

### Step 4 — Verify

Run the hard-constraint validator to confirm the (rule-based or reviewed) `enriched_papers.json` satisfies the contract:

```
python verify_enriched.py
# or:
python verify_enriched.py --input-dir ./output/2026-05-06_2349_jepa
```

Exit code `0` means clean; `1` means at least one violation (each printed with paper id + field name). The validator is stdlib-only and lives next to `extract.py`.

## Coordination with paper-search / paper-rank

`paper-extract` is a pure consumer of `paper-rank`'s output:

- **Reads** `<run_dir>/ranked_papers.json` (papers must already carry `rank` and `scores` from `paper-rank`).
- Uses `query.search_terms` from the top-level query to boost keyword scoring.
- **Writes** `<run_dir>/enriched_papers.json` (rule-based, then revised by the agent-review pass).
- **Writes** `<run_dir>/enriched_papers.rule_based.json` (one-time backup created at the start of the first review pass).
- **Does not** touch `latest_run.txt`, the staging `query.json`, or `cache/`.

Downstream skills (`paper-network`, `paper-report`, `follow-up`) read `enriched_papers.json` from the same `<run_dir>` and do not inspect `extraction_config`, so the review's metadata is invisible to them.

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

1. Top-level `extracted_at` and `extraction_config` (echoes flags + method, plus an optional `review` sub-block after the agent-review pass). For traceability only — no downstream skill reads `extraction_config`.
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
    "method": "rule_based_v2",
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
        "limitations": "We acknowledge the method is limited to short clips.",
        "evidence_sentences": {
          "contribution": "Our contribution is a JEPA model that ...",
          "method": "The latent predictor is trained with a masked objective ...",
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
| `main_contribution` | string | one verbatim sentence from `title + abstract` stating the paper's contribution; `""` if none |
| `method` | string | verbatim sentence describing the proposed method/framework; `""` only when the abstract has no description at all |
| `task` | string | verbatim sentence describing the task or application |
| `keywords` | string[] | up to `keyword_limit`, lowercased; every entry word-boundary matches in `title + abstract`; may include title bigrams |
| `datasets_or_domains` | string[] | dataset names / domain triggers detected via the acronym, mixed-case, and versioned-dataset patterns; never contains generic-acronym blacklist entries |
| `evaluation_signals` | string[] | verbatim sentences containing evaluation cues (accuracy, benchmark, outperforms, …) |
| `limitations` | string | verbatim sentence explicitly stating a limitation/failure/open challenge; `""` when the abstract states none |
| `evidence_sentences` | object | `{contribution, method, task}` → verbatim sentence corroborating each claim. Prefer the second cue-matching sentence; fall back to the claim sentence when only one match exists |

When a field cannot be filled, the script returns the field's empty default (`""` for strings, `[]` for lists); per-paper extraction never raises.

## Extraction methodology

Rule-based, stdlib only — no NLP libraries. Highlights of the `rule_based_v2` extractor (the agent-review pass then revises the output as documented in Step 3):

**Sentence splitter**: `re.split(r'(?<=[.!?])\s+(?=[A-Z])')`, with an abbreviation guard that masks the dots in `et al.`, `e.g.`, `i.e.`, `Fig.`, `Eq.`, `Sec.`, `Tab.`, `vs.`, `cf.`, `approx.`, `Dr.`, `Mr.` before splitting and restores them afterward. Eliminates over-splits on "Vaswani et al. We propose…" — the most common abstract abbreviation.

**Trigger lists** (matched as **case-insensitive whole-word regex** against each sentence — `\bcue\b`, with internal spaces in multi-word cues compiled as `\s+`. This avoids false positives like `Models` matching the cue `model`.):

- *Contribution*: `we propose`, `we present`, `we introduce`, `we develop`, `we design`, `we show`, `this paper proposes`, `this work presents`, `we contribute`, `our work provides`. The first sentence containing any cue is selected.
- *Method*: `method`, `model`, `framework`, `architecture`, `pipeline`, `approach`, `algorithm`, `objective`, `pretraining`, `self-supervised`, `masked`, `latent`, `embedding`, `retrieval`, `diffusion`, `encoder`, `decoder`, `transformer`, `convolutional`. Three-tier selection: (1) first cue match in the body, skipping the title sentence; (2) longest body sentence ≥8 tokens that is not the contribution sentence; (3) fall back to the contribution sentence.
- *Task*: `task`, `classification`, `retrieval`, `segmentation`, `prediction`, `generation`, `representation learning`, `video understanding`, `time-series`, `inpainting`, `detection`, `captioning`, `tracking`, `denoising`, `depth estimation`. First match (the title sentence is allowed because task words like `classification` legitimately appear in titles).
- *Limitations*: tiered. **Strong** cues — `limitation`, `limited`, `fails to`, `does not`, `cannot`, `drawback`, `shortcoming`, `we acknowledge` — trigger on the first match. **Weak** cues — `however`, `challenge(s)`, `constrained`, `fail`, `failure`, `unclear` — only trigger when they occur in the second half of the abstract body, so a discourse `However, we propose...` at the abstract's start is no longer mislabelled. If no cue matches, `""`.
- *Evaluation signals*: `outperform`, `improve`, `achieve`, `accuracy`, `f1`, `auroc`, `auc`, `map`, `benchmark`, `state-of-the-art`, `baseline`, `evaluation`, `experiment`, `ablation`, `roc`, `bleu`, `rouge`, `mse`, `psnr`, `ssim`, `dice`. All matching sentences, deduped, capped at 6.

**Keywords (TF-IDF weighted)**: tokenize each paper to `\b[a-z0-9][a-z0-9-]*\b`, drop a built-in stopword set (~80 common English words) and pure-digit / single-char tokens. The IDF is computed over the selected top-N corpus: `idf(t) = log((N + 1) / (df + 1))`. Score per unigram is `tf * (1 + idf) * title_boost * query_boost`, where `title_boost = 2` if the token appears in the title and `query_boost = 3` if a token from `query.search_terms` matches. Title bigrams are added with prior `5.0 * (1 + avg_idf)` so a bigram of two corpus-common words ranks lower than a bigram of two distinctive ones. Sort descending, keep `keyword_limit`. **Effect**: corpus-common tokens like `model`, `method`, `approach` no longer dominate top-3.

**Evidence sentences**: pick the **second** cue-matching sentence per family (contribution / method / task), and fall back to the corresponding claim sentence when only one match exists. The fallback preserves backward compatibility with the v1 contract on short abstracts.

**Datasets / domains**: union of four patterns, in order, deduplicated case-insensitively, capped at 10:

- Hyphenated versioned dataset names: `\b[A-Z][A-Za-z]+-\d+[A-Za-z]*\b` (catches `CIFAR-10`, `ImageNet-1K`, `COCO-2017`, `MNIST-1D`).
- All-caps acronyms: `\b[A-Z][A-Z0-9]{1,7}\b`, **excluding** a blacklist of generic research/hardware acronyms (`AI ML DL NLP CV NN GPU TPU CNN RNN DNN MLP LSTM SOTA ICLR NEURIPS CVPR ECCV ICCV ACL EMNLP`).
- Mixed-case dataset names: `\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b|\b[A-Z][a-zA-Z]*\d+[a-zA-Z]*\b` (catches `ImageNet`, `ETTh1`).
- Domain triggers (`dataset`, `benchmark`, `corpus`, `cohort`, `domain`, `video`, `image`, `eeg`, `sonar`, `medical`, `surgical`, `audio`, `text`) matched with `\b{trig}\b`, so `audio` no longer fires inside `audiobook`.

## Example

```bash
# After paper-rank has populated the latest run
python extract.py
# the agent then performs Step 3 (review) automatically; then:
python verify_enriched.py

# Pin a specific run, top-30, more keywords:
python extract.py --input-dir ./output/2026-05-06_2349_jepa --top-n 30 --keyword-limit 12
```

After running, `<input-dir>/enriched_papers.json` is the artifact for `paper-network` / `paper-report` / `follow-up`, and `<input-dir>/enriched_papers.rule_based.json` is the pre-review snapshot.

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
| **agent review** finds a field that cannot be made faithful | leave that field as `""`/`[]` rather than fabricating content |
| `verify_enriched.py` finds violations | exit code 1, prints one line per violation; the offending file is left in place for human inspection |

## Dependencies

- Python ≥ 3.9
- **No third-party dependencies** — stdlib only (`extract.py` and `verify_enriched.py`).

## Independent test hooks (for course evaluation)

- **count check** — `enriched.count == min(top_n, ranked.count)`.
- **schema preservation** — every per-paper field present in `ranked_papers.json` is also present in `enriched_papers.json` with the same value.
- **extraction completeness** — every paper has an `extraction` block with the eight expected keys (`main_contribution`, `method`, `task`, `keywords`, `datasets_or_domains`, `evaluation_signals`, `limitations`, `evidence_sentences`); `evidence_sentences` always has the three keys `contribution` / `method` / `task`.
- **type check** — `keywords` / `datasets_or_domains` / `evaluation_signals` are lists of strings; the rest of `extraction` are strings (or sub-objects of strings).
- **script stability** — running `extract.py` twice on the same `ranked_papers.json` produces a byte-identical `enriched_papers.json` (after stripping `extracted_at`). The agent-review pass is **not** required to be byte-identical across sessions, but the rule-based snapshot in `enriched_papers.rule_based.json` always is.
- **faithfulness (rule-based)** — `python verify_enriched.py` exits 0 on the rule-based output: every keyword / dataset / sentence is grounded in the paper's own `title + abstract`.
- **faithfulness (reviewed)** — `python verify_enriched.py` exits 0 on the reviewed output as well; the review pass must not introduce ungrounded content.
- **acronym hygiene** — `datasets_or_domains` contains no entries from the generic acronym blacklist (`AI ML DL NLP CV NN GPU TPU CNN RNN DNN MLP LSTM SOTA ICLR NEURIPS CVPR ECCV ICCV ACL EMNLP`).
- **tiered limitation** — for an abstract starting with `However, we propose...` but containing no explicit limitation language, `limitations == ""`.
- **TF-IDF de-noising** — top-3 keywords for any paper do not contain corpus-common tokens like `model`, `method`, `approach`, `propose` (these accumulate IDF ≈ 0 when present in many papers and thus rank below distinctive terms).
- **empty handling** — when `ranked_papers.json` has `papers: []`, the script writes `enriched_papers.json` with `papers: []` and exits 0; the agent-review pass and verifier both treat it as a clean run.
- **review backup** — if any agent review has been performed, `enriched_papers.rule_based.json` exists alongside `enriched_papers.json` and can be diffed for rule-vs-reviewed comparison.
