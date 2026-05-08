---
name: follow-up
description: "Answer follow-up questions about the current arXiv briefing using only cached run artifacts such as briefing.json, enriched_papers.json, ranked_papers.json, and graph_metrics.json. Trigger phrases: 'follow-up', 'ask about this briefing', 'tell me more about rank 1', 'compare paper A and B', '追问论文', '运行 follow-up'."
author: dreamyingy
version: 1.0.0
tags:
  - arxiv
  - qa
  - briefing
  - grounded
---

# follow-up

Stage 6 of the daily arXiv briefing agent. Answers follow-up questions using only the cached JSON artifacts in the current run directory. It does **not** call arXiv, does **not** call an LLM, and does **not** invent missing paper facts.

## Workflow

### Step 1 — Locate the run directory

- If `--input-dir <path>` is given, use it directly.
- Otherwise, read `./output/latest_run.txt` and use `./output/<run_id>/`.

The directory must contain `briefing.json`. The script also reads `ranked_papers.json`, `enriched_papers.json`, and `graph_metrics.json` when available to answer beyond the report's top-N.

### Step 2 — Ask a question

```bash
python followup.py "tell me more about rank 1"
python followup.py "compare rank 1 and rank 3"
python followup.py "which papers are most novel?"
python followup.py --input-dir ./output/2026-05-07_1733_jepa-representation-learning-c --question "哪些论文和 JEPA 最相关？"
```

If no positional question or `--question` is passed, the script reads stdin.

Optional flags:

| flag | type | default | notes |
|---|---|---|---|
| `--input-dir` | path | resolved from `latest_run.txt` | per-run directory |
| `--question` | string | none | follow-up question |
| `--top-k` | int | `5` | number of papers for list/search answers |
| `--save` | bool | false | append question/answer JSON to `followups.jsonl` |

## Supported question shapes

- **Paper detail**: `tell me more about rank 1`, `paper 2502.18056`, title substring.
- **Comparison**: `compare rank 1 and rank 3`, `compare 2502.18056 vs 2604.10591`.
- **Network highlights**: questions containing `novel`, `bridging`, `central`, `pagerank`.
- **Relevance / keyword search**: questions such as `which papers are most related to JEPA?`; the script searches title, keywords, contribution, method, task, and abstract snippets from cached files.
- **Fallback summary**: if the question is broad or ambiguous, show the top ranked papers and suggest stable identifiers the user can ask about.

## Grounding rules

- Only use fields present in cached JSON files.
- If extraction or graph metrics are missing, state that the field is unavailable.
- Cite paper IDs, ranks, and URLs whenever possible.
- Preserve evidence sentences from `extraction.evidence_sentences` for contribution/method/task claims.
- Do not infer claims from outside the cached artifacts.

## Output

Markdown answer to stdout. With `--save`, append a record to:

```text
<run_dir>/followups.jsonl
```

Each JSONL row contains:

```json
{
  "asked_at": "2026-05-07T10:00:00+00:00",
  "question": "tell me more about rank 1",
  "answer": "..."
}
```

## Error handling

| condition | behavior |
|---|---|
| `latest_run.txt` missing and `--input-dir` not passed | exit non-zero with hint to run upstream skills |
| `briefing.json` missing | exit non-zero with hint to run `paper-report` first |
| malformed JSON | exit non-zero with parser message |
| no question provided via args or stdin | exit non-zero |
| requested paper not found | answer with available top ranked identifiers |
| comparison has fewer than two identifiable papers | answer with a clarification-style message and top identifiers |

## Dependencies

- Python ≥ 3.9
- No third-party dependencies.

## Independent test hooks

- **detail check** — `rank 1` answer includes the rank-1 paper ID, title, URL, contribution, method, and evidence when available.
- **compare check** — comparison answer includes two paper IDs and method/task/contribution rows.
- **grounding check** — answer text contains only paper fields from cached JSON.
- **save check** — `--save` appends exactly one JSONL row.