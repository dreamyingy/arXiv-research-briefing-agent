#!/usr/bin/env python3
"""verify_enriched: hard-constraint validator for enriched_papers.json.

Used in two places:

1. As an agent self-check after the agent-review pass overwrites
   enriched_papers.json (see paper-extract/SKILL.md "Agent review pass").
2. As a smoke test that any enriched_papers.json (rule-based or reviewed)
   satisfies the project's extraction contract.

Checks (per paper):
- Schema: every paper has an `extraction` block with the 8 expected keys,
  `evidence_sentences` has the 3 expected sub-keys, list/string types
  match the canonical schema.
- Faithfulness: each keyword / dataset / sentence is grounded in the
  paper's own (title + abstract). Tokens must word-boundary match
  (case-insensitive); sentences must appear verbatim (whitespace-collapsed)
  in title + abstract.
- Acronym hygiene: datasets_or_domains MUST NOT contain entries from the
  generic-acronym blacklist.

Exit code 0 = clean; 1 = at least one violation; 2 = file/IO error.
Prints a human-readable report to stdout; counts to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Must stay in sync with extract.py:ACRONYM_BLACKLIST.
ACRONYM_BLACKLIST = {
    "AI", "ML", "DL", "NLP", "CV", "NN", "GPU", "TPU",
    "CNN", "RNN", "DNN", "MLP", "LSTM", "SOTA",
    "ICLR", "NEURIPS", "CVPR", "ECCV", "ICCV", "ACL", "EMNLP",
}

EXPECTED_EXTRACTION_KEYS = {
    "main_contribution", "method", "task", "keywords",
    "datasets_or_domains", "evaluation_signals", "limitations",
    "evidence_sentences",
}
EXPECTED_EVIDENCE_KEYS = {"contribution", "method", "task"}

WS_RE = re.compile(r"\s+")


def normalize_ws(s: str) -> str:
    return WS_RE.sub(" ", s).strip()


def token_in_text(token: str, text: str) -> bool:
    """Whole-word, case-insensitive presence of token in text.

    A multi-word token must appear with whitespace between its parts;
    internal hyphens are honored literally.
    """
    if not token:
        return False
    # Treat any run of whitespace inside the token as `\s+`.
    parts = re.escape(token).replace(r"\ ", r"\s+")
    return re.search(rf"\b{parts}\b", text, re.IGNORECASE) is not None


def sentence_in_text(sentence: str, text: str) -> bool:
    """A sentence is verbatim-grounded if it (modulo whitespace and one
    trailing `.!?`) appears as a substring of (title + abstract). The trailing
    punctuation tolerance is needed because extract.py joins title and
    abstract with a synthetic `". "` separator so the title can be the first
    sentence; that adds a period to the title sentence that is absent from
    the raw `title` / `abstract` fields.
    """
    if not sentence:
        return True  # empty string is a valid "no match" output
    s = normalize_ws(sentence).rstrip(".!?").strip()
    return s in normalize_ws(text)


def check_paper(paper: dict) -> list[str]:
    """Return a list of human-readable violations for one paper. Empty = OK."""
    pid = paper.get("id", "?")
    violations: list[str] = []
    ext = paper.get("extraction")
    if not isinstance(ext, dict):
        return [f"{pid}: missing or non-dict `extraction` block"]

    missing = EXPECTED_EXTRACTION_KEYS - set(ext.keys())
    if missing:
        violations.append(f"{pid}: extraction missing keys: {sorted(missing)}")

    # Type checks.
    for k in ("main_contribution", "method", "task", "limitations"):
        v = ext.get(k, "")
        if not isinstance(v, str):
            violations.append(f"{pid}: extraction.{k} must be str, got {type(v).__name__}")
    for k in ("keywords", "datasets_or_domains", "evaluation_signals"):
        v = ext.get(k, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            violations.append(f"{pid}: extraction.{k} must be list[str]")
    ev = ext.get("evidence_sentences", {})
    if not isinstance(ev, dict):
        violations.append(f"{pid}: extraction.evidence_sentences must be dict")
        ev = {}
    else:
        ev_missing = EXPECTED_EVIDENCE_KEYS - set(ev.keys())
        if ev_missing:
            violations.append(
                f"{pid}: evidence_sentences missing keys: {sorted(ev_missing)}")
        for k in EXPECTED_EVIDENCE_KEYS:
            if k in ev and not isinstance(ev[k], str):
                violations.append(
                    f"{pid}: evidence_sentences.{k} must be str, "
                    f"got {type(ev[k]).__name__}")

    # Grounding: build a corpus from this paper's own title + abstract.
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    corpus = title + " " + abstract

    # Sentence-level grounding.
    for k in ("main_contribution", "method", "task", "limitations"):
        v = ext.get(k, "")
        if isinstance(v, str) and not sentence_in_text(v, corpus):
            violations.append(
                f"{pid}: extraction.{k} not found verbatim in title+abstract")
    for k in EXPECTED_EVIDENCE_KEYS:
        v = ev.get(k, "") if isinstance(ev, dict) else ""
        if isinstance(v, str) and not sentence_in_text(v, corpus):
            violations.append(
                f"{pid}: evidence_sentences.{k} not found verbatim in title+abstract")
    for i, sent in enumerate(ext.get("evaluation_signals", []) or []):
        if isinstance(sent, str) and not sentence_in_text(sent, corpus):
            violations.append(
                f"{pid}: evaluation_signals[{i}] not found verbatim in title+abstract")

    # Token-level grounding.
    for i, kw in enumerate(ext.get("keywords", []) or []):
        if isinstance(kw, str) and not token_in_text(kw, corpus):
            violations.append(
                f"{pid}: keywords[{i}]={kw!r} not found as whole word in title+abstract")
    for i, ds in enumerate(ext.get("datasets_or_domains", []) or []):
        if not isinstance(ds, str):
            continue
        if ds.upper() in ACRONYM_BLACKLIST:
            violations.append(
                f"{pid}: datasets_or_domains[{i}]={ds!r} is in the "
                f"generic-acronym blacklist")
        if not token_in_text(ds, corpus):
            violations.append(
                f"{pid}: datasets_or_domains[{i}]={ds!r} not found as whole word "
                f"in title+abstract")

    return violations


def resolve_input_dir(input_dir_flag: Path | None) -> Path:
    if input_dir_flag is not None:
        if not input_dir_flag.is_dir():
            sys.exit(f"ERROR: --input-dir not found or not a directory: {input_dir_flag}")
        return input_dir_flag
    latest = Path("output") / "latest_run.txt"
    if not latest.exists():
        sys.exit(
            "ERROR: ./output/latest_run.txt not found. "
            "Pass --input-dir <path> to verify a specific run."
        )
    run_id = latest.read_text(encoding="utf-8").strip()
    run_dir = Path("output") / run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: run directory referenced by latest_run.txt is missing: {run_dir}")
    return run_dir


def main() -> int:
    p = argparse.ArgumentParser(
        description="Validate enriched_papers.json against the extraction contract")
    p.add_argument("--input-dir", type=Path, default=None,
                   help="Run directory containing enriched_papers.json "
                        "(default: resolved from ./output/latest_run.txt)")
    p.add_argument("--file", type=Path, default=None,
                   help="Validate this specific file instead of "
                        "<input-dir>/enriched_papers.json")
    args = p.parse_args()

    if args.file is not None:
        target = args.file
    else:
        run_dir = resolve_input_dir(args.input_dir)
        target = run_dir / "enriched_papers.json"
    if not target.exists():
        sys.exit(f"ERROR: file not found: {target}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {target} is not valid JSON: {e}")

    papers = data.get("papers", [])
    if not isinstance(papers, list):
        sys.exit(f"ERROR: {target} has non-list `papers` field")

    all_violations: list[str] = []
    for paper in papers:
        all_violations.extend(check_paper(paper))

    if all_violations:
        print(f"FAIL: {len(all_violations)} violation(s) in {target}")
        for v in all_violations:
            print(f"  - {v}")
        print(f"\nChecked {len(papers)} paper(s).", file=sys.stderr)
        return 1
    print(f"OK: {len(papers)} paper(s) in {target} satisfy the extraction contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
