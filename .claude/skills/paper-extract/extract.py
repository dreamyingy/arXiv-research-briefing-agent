#!/usr/bin/env python3
"""paper-extract: rule-based information extraction for the daily arXiv briefing agent.

Reads <input-dir>/ranked_papers.json, processes top-N papers, writes
<input-dir>/enriched_papers.json. No third-party deps, no LLM, no PDF parsing.
"""
from __future__ import annotations

import argparse
import json
import math
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
    "encoder", "decoder", "transformer", "convolutional",
]
TASK_CUES = [
    "task", "classification", "retrieval", "segmentation", "prediction",
    "generation", "representation learning", "video understanding",
    "time-series", "inpainting",
    "detection", "captioning", "tracking", "denoising", "depth estimation",
]
# Limitations: tiered. Strong cues mean the paper itself states a limitation;
# weak cues (e.g. discourse "however") only count when they appear in the
# second half of the abstract, where real limitations tend to live.
LIMITATION_CUES_STRONG = [
    "limitation", "limited", "fails to", "does not", "cannot",
    "drawback", "shortcoming", "we acknowledge",
]
LIMITATION_CUES_WEAK = [
    "however", "challenge", "challenges", "constrained", "fail", "failure",
    "unclear",
]
EVALUATION_CUES = [
    "outperform", "improve", "achieve", "accuracy", "f1", "auroc", "auc",
    "map", "benchmark", "state-of-the-art", "baseline", "evaluation",
    "experiment",
    "ablation", "roc", "bleu", "rouge", "mse", "psnr", "ssim", "dice",
]
DOMAIN_TRIGGERS = [
    "dataset", "benchmark", "corpus", "cohort", "domain",
    "video", "image", "eeg", "sonar", "medical", "surgical", "audio", "text",
]
# Generic research / hardware acronyms that should not appear in
# datasets_or_domains; they pollute the field without identifying a dataset.
ACRONYM_BLACKLIST = {
    "AI", "ML", "DL", "NLP", "CV", "NN", "GPU", "TPU",
    "CNN", "RNN", "DNN", "MLP", "LSTM", "SOTA",
    "ICLR", "NEURIPS", "CVPR", "ECCV", "ICCV", "ACL", "EMNLP",
}
# Common English abbreviations whose internal dot must NOT split sentences.
ABBREVIATIONS = [
    "et al.", "e.g.", "i.e.", "Fig.", "Eq.", "Sec.", "Tab.",
    "vs.", "cf.", "approx.", "Dr.", "Mr.",
]
_ABBR_DOT = "\x00DOT\x00"

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
# Hyphenated dataset names: CIFAR-10, ImageNet-1K, COCO-2017, MNIST-1D, ...
DATASET_VERSIONED_RE = re.compile(r"\b[A-Z][A-Za-z]+-\d+[A-Za-z]*\b")


def _compile_cues(cues: list[str]) -> list[re.Pattern]:
    pats: list[re.Pattern] = []
    for cue in cues:
        body = re.escape(cue).replace(r"\ ", r"\s+")
        pats.append(re.compile(rf"\b{body}\b", re.IGNORECASE))
    return pats


CONTRIBUTION_PATS = _compile_cues(CONTRIBUTION_CUES)
METHOD_PATS = _compile_cues(METHOD_CUES)
TASK_PATS = _compile_cues(TASK_CUES)
LIMITATION_STRONG_PATS = _compile_cues(LIMITATION_CUES_STRONG)
LIMITATION_WEAK_PATS = _compile_cues(LIMITATION_CUES_WEAK)
EVALUATION_PATS = _compile_cues(EVALUATION_CUES)


def split_sentences(text: str) -> list[str]:
    """Sentence-split with abbreviation guard.

    The naive `(?<=[.!?])\\s+(?=[A-Z])` splitter over-cuts at common research
    abbreviations like "et al.", "Fig.", "e.g." when the next clause starts
    with a capital letter. We mask their internal dots with a sentinel before
    splitting and restore them afterward.
    """
    if not text:
        return []
    masked = text
    for abbr in ABBREVIATIONS:
        masked_abbr = abbr.replace(".", _ABBR_DOT)
        masked = re.sub(re.escape(abbr), masked_abbr, masked, flags=re.IGNORECASE)
    parts = [s.strip() for s in SENT_SPLIT_RE.split(masked) if s.strip()]
    return [s.replace(_ABBR_DOT, ".") for s in parts]


def find_first_with_cue(sentences: list[str], patterns: list[re.Pattern],
                        skip: int = 0) -> str:
    for sent in sentences[skip:]:
        if any(p.search(sent) for p in patterns):
            return sent
    return ""


def find_nth_with_cue(sentences: list[str], patterns: list[re.Pattern],
                      n: int, skip: int = 0) -> str:
    """Return the n-th (0-indexed) sentence containing any cue, else ""."""
    matches: list[str] = []
    for sent in sentences[skip:]:
        if any(p.search(sent) for p in patterns):
            matches.append(sent)
            if len(matches) > n:
                return matches[n]
    return matches[n] if len(matches) > n else ""


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


def find_method_sentence(sentences: list[str], contrib_sent: str) -> str:
    """Choose the method sentence with a 3-tier fallback.

    1. First method-cue match in the body (skip title sentence).
    2. Longest body sentence that is not the contribution sentence.
       Rationale: in abstracts without an explicit "method"/"framework" cue,
       the descriptive method sentence is usually the longest non-title one.
    3. Fall back to the contribution sentence (legacy behavior).
    """
    method_sent = find_first_with_cue(sentences, METHOD_PATS, skip=1)
    if method_sent:
        return method_sent
    body = sentences[1:] if len(sentences) > 1 else sentences
    candidates = [s for s in body if s != contrib_sent and len(s.split()) >= 8]
    if candidates:
        return max(candidates, key=len)
    return contrib_sent


def find_limitation_sentence(sentences: list[str]) -> str:
    """Tiered limitation pick.

    Strong cues (e.g. "fails to", "limitation", "we acknowledge") trigger
    immediately. Weak cues (e.g. "however", "challenge") only trigger when
    they appear in the second half of the abstract body; a discourse
    "However, we propose..." at the start of an abstract is not a limitation.
    """
    for sent in sentences:
        if any(p.search(sent) for p in LIMITATION_STRONG_PATS):
            return sent
    if len(sentences) <= 2:
        return ""
    # Skip title (index 0) and the first half of the body sentences.
    body_start = 1
    body_len = len(sentences) - body_start
    half = body_start + body_len // 2
    for sent in sentences[half:]:
        if any(p.search(sent) for p in LIMITATION_WEAK_PATS):
            return sent
    return ""


def compute_idf(papers: list[dict]) -> dict[str, float]:
    """Inverse document frequency over the selected top-N corpus.

    df is the number of papers whose title+abstract contains the token
    (counted once per paper, irrespective of in-paper frequency). Returns
    log((N + 1) / (df + 1)); unseen tokens get 0.0 from the caller's .get().
    """
    n = len(papers)
    if n == 0:
        return {}
    df: Counter[str] = Counter()
    for p in papers:
        text = ((p.get("title") or "") + " " + (p.get("abstract") or "")).lower()
        seen_in_doc: set[str] = set()
        for tok in TOKEN_RE.findall(text):
            if tok in STOPWORDS or tok.isdigit() or len(tok) <= 1:
                continue
            if tok in seen_in_doc:
                continue
            seen_in_doc.add(tok)
            df[tok] += 1
    return {tok: math.log((n + 1) / (c + 1)) for tok, c in df.items()}


def extract_keywords(title: str, abstract: str, search_terms: list[str],
                     limit: int, idf: dict[str, float]) -> list[str]:
    """TF-IDF-weighted keyword extraction with title + query boosts.

    Score per unigram: ``tf * (1 + idf) * title_boost * query_boost``.
    Title bigrams keep their high prior but are scaled by the average IDF of
    their two tokens, so a bigram of two corpus-common words ranks lower than
    a bigram of two distinctive ones.
    """
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
        score = float(count) * (1.0 + idf.get(tok, 0.0))
        if tok in title_tokens:
            score *= 2
        if tok in boost_terms:
            score *= 3
        scored.append((tok, score))

    # Title bigrams must be verbatim-adjacent (only whitespace between the two
    # tokens). Iterating over the stopword-stripped sequence would jump across
    # stopwords; iterating over the raw token list still misses punctuation
    # separators (e.g. `GeoMeld: Toward` tokenizes as ['geomeld','toward']
    # but the colon means the two words are not adjacent in the title).
    raw_title_tokens = TOKEN_RE.findall(title_low)
    seen_bigrams: set[str] = set()
    for i in range(len(raw_title_tokens) - 1):
        t1, t2 = raw_title_tokens[i], raw_title_tokens[i + 1]
        if (t1 in STOPWORDS or t2 in STOPWORDS or
                len(t1) <= 1 or len(t2) <= 1 or
                t1.isdigit() or t2.isdigit()):
            continue
        adj_pat = re.compile(
            rf"\b{re.escape(t1)}\s+{re.escape(t2)}\b", re.IGNORECASE)
        if not adj_pat.search(title_low):
            continue
        bg = f"{t1} {t2}"
        if bg in seen_bigrams:
            continue
        seen_bigrams.add(bg)
        avg_idf = (idf.get(t1, 0.0) + idf.get(t2, 0.0)) / 2
        scored.append((bg, 5.0 * (1.0 + avg_idf)))

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

    # Hyphenated versioned dataset names first (CIFAR-10, ImageNet-1K, ...).
    for m in DATASET_VERSIONED_RE.findall(text):
        key = m.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(m)
    # All-caps acronyms, skipping generic research/hardware abbreviations.
    for m in ACRONYM_RE.findall(text):
        if m.upper() in ACRONYM_BLACKLIST:
            continue
        key = m.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(m)
    # Mixed-case dataset names (ImageNet, MiniImageNet, ETTh1, ...).
    for m in MIXED_RE.findall(text):
        key = m.lower()
        if key not in seen_lower:
            seen_lower.add(key)
            out.append(m)
    # Domain triggers — word-boundary match so "audio" doesn't fire on
    # "audiobook" and "image" doesn't fire on "imagery".
    for trig in DOMAIN_TRIGGERS:
        pat = re.compile(rf"\b{re.escape(trig)}\b", re.IGNORECASE)
        key = trig.lower()
        if pat.search(text) and key not in seen_lower:
            seen_lower.add(key)
            out.append(trig)

    return out[:cap]


def extract_for_paper(paper: dict, search_terms: list[str],
                      keyword_limit: int, idf: dict[str, float]) -> dict:
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    if not abstract.strip():
        print(f"WARN: paper {paper.get('id', '?')} has empty abstract",
              file=sys.stderr)

    full_text = title + ". " + abstract
    sentences = split_sentences(full_text)

    contrib_sent = find_first_with_cue(sentences, CONTRIBUTION_PATS)
    method_sent = find_method_sentence(sentences, contrib_sent)
    task_sent = find_first_with_cue(sentences, TASK_PATS)
    limit_sent = find_limitation_sentence(sentences)
    eval_sents = find_all_with_cue(sentences, EVALUATION_PATS, cap=6)

    # Evidence sentences: pick the *second* cue-matching sentence when one
    # exists, so evidence is a corroborating second statement rather than a
    # verbatim copy of the claim. When only one match exists (short abstract),
    # fall back to the claim sentence — preserves the v1 contract.
    contrib_evidence = find_nth_with_cue(sentences, CONTRIBUTION_PATS, n=1) \
        or contrib_sent
    method_evidence = find_nth_with_cue(sentences, METHOD_PATS, n=1, skip=1) \
        or method_sent
    task_evidence = find_nth_with_cue(sentences, TASK_PATS, n=1) or task_sent

    return {
        "main_contribution": contrib_sent,
        "method": method_sent,
        "task": task_sent,
        "keywords": extract_keywords(title, abstract, search_terms,
                                     keyword_limit, idf),
        "datasets_or_domains": extract_datasets_or_domains(abstract),
        "evaluation_signals": eval_sents,
        "limitations": limit_sent,
        "evidence_sentences": {
            "contribution": contrib_evidence,
            "method": method_evidence,
            "task": task_evidence,
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
        "method": "rule_based_v2",
        "top_n": args.top_n,
        "keyword_limit": args.keyword_limit,
        "source_fields": ["title", "abstract"],
    }

    selected = papers[: args.top_n]
    idf = compute_idf(selected)
    enriched: list[dict] = []
    for paper in selected:
        ext = extract_for_paper(paper, search_terms, args.keyword_limit, idf)
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
