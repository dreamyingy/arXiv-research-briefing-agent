#!/usr/bin/env python3
"""paper-extract: rule-based information extraction for the daily arXiv briefing agent.

Reads <input-dir>/ranked_papers.json, processes top-N papers, writes
<input-dir>/enriched_papers.json. No third-party deps, no LLM, no PDF parsing.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


CONTRIBUTION_CUES = [
    "we propose", "we present", "we introduce", "we develop",
    "we design", "we show", "this paper proposes",
    "this work presents", "we contribute", "our work provides",
]
METHOD_CUES = [
    "method", "model", "framework", "architecture", "pipeline", "approach",
    "algorithm", "objective", "pretraining", "self-supervised", "masked",
    "latent", "embedding", "retrieval", "diffusion",
]
TASK_CUES = [
    "task", "classification", "retrieval", "segmentation", "prediction",
    "generation", "representation learning", "video understanding",
    "time-series", "inpainting",
]
LIMITATION_CUES = [
    "however", "limitation", "limited", "challenge", "fail", "failure",
    "unclear", "constrained",
]
EVALUATION_CUES = [
    "outperform", "improve", "achieve", "accuracy", "f1", "auroc", "auc",
    "map", "benchmark", "state-of-the-art", "baseline", "evaluation",
    "experiment",
]
DOMAIN_TRIGGERS = [
    "dataset", "benchmark", "corpus", "cohort", "domain",
    "video", "image", "eeg", "sonar", "medical", "surgical", "audio", "text",
]

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "had", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "we", "were", "with", "which", "while",
    "when", "where", "who", "what", "how", "why", "our", "their", "they",
    "them", "these", "those", "such", "using", "used", "based", "also", "can",
    "may", "more", "than", "not", "no", "both", "other", "one", "two", "each",
    "any", "all", "very", "most", "some", "new", "via", "through", "over",
    "between", "among", "without", "within", "across", "about", "upon",
    "should", "would", "could", "will", "shall", "might", "must", "do",
    "does", "did", "been", "being", "there", "here", "however", "thus",
    "hence", "i", "you", "he", "she", "his", "her", "its", "their", "if",
    "then", "so", "yet", "still", "only", "just", "even",
}

SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
MIXED_RE = re.compile(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b|\b[A-Z][a-zA-Z]*\d+[a-zA-Z]*\b")


def _compile_cues(cues: list[str]) -> list[re.Pattern]:
    pats: list[re.Pattern] = []
    for cue in cues:
        body = re.escape(cue).replace(r"\ ", r"\s+")
        pats.append(re.compile(rf"\b{body}\b", re.IGNORECASE))
    return pats


CONTRIBUTION_PATS = _compile_cues(CONTRIBUTION_CUES)
METHOD_PATS = _compile_cues(METHOD_CUES)
TASK_PATS = _compile_cues(TASK_CUES)
LIMITATION_PATS = _compile_cues(LIMITATION_CUES)
EVALUATION_PATS = _compile_cues(EVALUATION_CUES)


def split_sentences(text: str) -> list[str]:
    if not text:
        return []
    return [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]


def find_first_with_cue(sentences: list[str], patterns: list[re.Pattern],
                        skip: int = 0) -> str:
    for sent in sentences[skip:]:
        if any(p.search(sent) for p in patterns):
            return sent
    return ""


def find_all_with_cue(sentences: list[str], patterns: list[re.Pattern],
                      cap: int) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sent in sentences:
        if any(p.search(sent) for p in patterns) and sent not in seen:
            seen.add(sent)
            out.append(sent)
            if len(out) >= cap:
                break
    return out


def extract_keywords(title: str, abstract: str, search_terms: list[str],
                     limit: int) -> list[str]:
    title_low = title.lower()
    abs_low = abstract.lower()
    text = title_low + " " + abs_low

    tokens = [t for t in TOKEN_RE.findall(text)
              if t not in STOPWORDS and not t.isdigit() and len(t) > 1]
    counts = Counter(tokens)

    title_tokens = set(t for t in TOKEN_RE.findall(title_low)
                       if t not in STOPWORDS and len(t) > 1)
    boost_terms: set[str] = set()
    for st in search_terms:
        for tok in TOKEN_RE.findall(st.lower()):
            if tok not in STOPWORDS and len(tok) > 1:
                boost_terms.add(tok)

    scored: list[tuple[str, float]] = []
    for tok, count in counts.items():
        score = float(count)
        if tok in title_tokens:
            score *= 2
        if tok in boost_terms:
            score *= 3
        scored.append((tok, score))

    title_seq = [t for t in TOKEN_RE.findall(title_low)
                 if t not in STOPWORDS and len(t) > 1]
    seen_bigrams: set[str] = set()
    for i in range(len(title_seq) - 1):
        bg = f"{title_seq[i]} {title_seq[i+1]}"
        if bg not in seen_bigrams:
            seen_bigrams.add(bg)
            scored.append((bg, 5.0))

    scored.sort(key=lambda x: (-x[1], x[0]))

    seen: set[str] = set()
    selected: list[str] = []
    for tok, _ in scored:
        if tok in seen:
            continue
        seen.add(tok)
        selected.append(tok)
        if len(selected) >= limit:
            break
    return selected


def extract_datasets_or_domains(text: str, cap: int = 10) -> list[str]:
    seen_lower: set[str] = set()
    out: list[str] = []

    for m in ACRONYM_RE.findall(text):
        key = m.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(m)
    for m in MIXED_RE.findall(text):
        key = m.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(m)

    low = text.lower()
    for trig in DOMAIN_TRIGGERS:
        if trig in low and trig not in seen_lower:
            seen_lower.add(trig)
            out.append(trig)

    return out[:cap]


def extract_for_paper(paper: dict, search_terms: list[str],
                      keyword_limit: int) -> dict:
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    if not abstract.strip():
        print(f"WARN: paper {paper.get('id', '?')} has empty abstract",
              file=sys.stderr)

    full_text = title + ". " + abstract
    sentences = split_sentences(full_text)

    contrib_sent = find_first_with_cue(sentences, CONTRIBUTION_PATS)
    # Skip the title sentence for method to avoid false matches like 'Models' -> 'model'
    method_sent = find_first_with_cue(sentences, METHOD_PATS, skip=1)
    task_sent = find_first_with_cue(sentences, TASK_PATS)
    limit_sent = find_first_with_cue(sentences, LIMITATION_PATS)
    eval_sents = find_all_with_cue(sentences, EVALUATION_PATS, cap=6)

    if not method_sent:
        method_sent = contrib_sent

    return {
        "main_contribution": contrib_sent,
        "method": method_sent,
        "task": task_sent,
        "keywords": extract_keywords(title, abstract, search_terms, keyword_limit),
        "datasets_or_domains": extract_datasets_or_domains(abstract),
        "evaluation_signals": eval_sents,
        "limitations": limit_sent,
        "evidence_sentences": {
            "contribution": contrib_sent,
            "method": method_sent,
            "task": task_sent,
        },
    }


def resolve_input_dir(input_dir_flag: Path | None) -> Path:
    if input_dir_flag is not None:
        if not input_dir_flag.is_dir():
            sys.exit(f"ERROR: --input-dir not found or not a directory: {input_dir_flag}")
        return input_dir_flag
    latest = Path("output") / "latest_run.txt"
    if not latest.exists():
        sys.exit(
            "ERROR: ./output/latest_run.txt not found. "
            "Run paper-search then paper-rank first, or pass --input-dir."
        )
    run_id = latest.read_text(encoding="utf-8").strip()
    run_dir = Path("output") / run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: run directory referenced by latest_run.txt is missing: {run_dir}")
    return run_dir


def load_ranked(run_dir: Path) -> dict:
    p = run_dir / "ranked_papers.json"
    if not p.exists():
        sys.exit(
            f"ERROR: ranked_papers.json not found in {run_dir}. "
            "Run paper-rank first."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: ranked_papers.json is not valid JSON: {e}")
    if "papers" not in data:
        sys.exit("ERROR: ranked_papers.json missing required key 'papers'")
    return data


def main() -> int:
    p = argparse.ArgumentParser(
        description="Rule-based extraction of structured info from top-N ranked papers")
    p.add_argument("--input-dir", type=Path, default=None,
                   help="Run directory containing ranked_papers.json "
                        "(default: resolved from ./output/latest_run.txt)")
    p.add_argument("--top-n", type=int, default=20,
                   help="How many top-ranked papers to process (default 20)")
    p.add_argument("--keyword-limit", type=int, default=8,
                   help="Max keywords per paper (default 8)")
    args = p.parse_args()

    if args.top_n <= 0:
        sys.exit(f"ERROR: --top-n must be positive, got {args.top_n}")
    if args.keyword_limit <= 0:
        sys.exit(f"ERROR: --keyword-limit must be positive, got {args.keyword_limit}")

    run_dir = resolve_input_dir(args.input_dir)
    ranked = load_ranked(run_dir)
    papers = ranked["papers"]
    search_terms = ranked.get("query", {}).get("search_terms", [])

    config = {
        "method": "rule_based_v1",
        "top_n": args.top_n,
        "keyword_limit": args.keyword_limit,
        "source_fields": ["title", "abstract"],
    }

    selected = papers[: args.top_n]
    enriched: list[dict] = []
    for paper in selected:
        ext = extract_for_paper(paper, search_terms, args.keyword_limit)
        enriched.append({**paper, "extraction": ext})

    payload = {
        "query": ranked.get("query"),
        "fetched_at": ranked.get("fetched_at"),
        "ranked_at": ranked.get("ranked_at"),
        "ranking_config": ranked.get("ranking_config"),
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "extraction_config": config,
        "count": len(enriched),
        "papers": enriched,
    }
    out_path = run_dir / "enriched_papers.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not enriched:
        print(f"WARN: empty corpus -> {out_path}", file=sys.stderr)
    else:
        print(f"INFO: extracted {len(enriched)} papers -> {out_path}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
