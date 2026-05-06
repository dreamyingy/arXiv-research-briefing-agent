#!/usr/bin/env python3
"""paper-rank: BM25 + recency ranking for the daily arXiv briefing agent.

Reads <input-dir>/raw_papers.json, scores every paper, writes
<input-dir>/ranked_papers.json next to it. Does not create a new run dir
and does not touch latest_run.txt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from rank_bm25 import BM25Okapi
except ImportError:
    sys.exit("ERROR: 'rank_bm25' not installed. Run: pip install rank_bm25")


TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def resolve_input_dir(input_dir_flag: Path | None) -> Path:
    if input_dir_flag is not None:
        if not input_dir_flag.is_dir():
            sys.exit(f"ERROR: --input-dir not found or not a directory: {input_dir_flag}")
        return input_dir_flag
    latest = Path("output") / "latest_run.txt"
    if not latest.exists():
        sys.exit(
            "ERROR: ./output/latest_run.txt not found. "
            "Run paper-search first, or pass --input-dir explicitly."
        )
    run_id = latest.read_text(encoding="utf-8").strip()
    run_dir = Path("output") / run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: run directory referenced by latest_run.txt is missing: {run_dir}")
    return run_dir


def load_raw(run_dir: Path) -> dict:
    raw_path = run_dir / "raw_papers.json"
    if not raw_path.exists():
        sys.exit(f"ERROR: raw_papers.json not found in {run_dir}")
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: raw_papers.json is not valid JSON: {e}")
    if "query" not in data or "papers" not in data:
        sys.exit("ERROR: raw_papers.json missing required keys 'query' / 'papers'")
    return data


def build_query_tokens(query: dict) -> list[str]:
    parts = [query.get("normalized_query", "")]
    parts.extend(query.get("search_terms", []))
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokenize(" ".join(parts)):
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def build_doc_tokens(paper: dict) -> list[str]:
    pieces = [paper.get("title", ""), paper.get("abstract", "")]
    pieces.extend(paper.get("categories", []))
    return tokenize(" ".join(pieces))


def minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def recency_scores(papers: list[dict]) -> list[float]:
    ords = [datetime.strptime(p["published"], "%Y-%m-%d").toordinal() for p in papers]
    lo, hi = min(ords), max(ords)
    if hi == lo:
        return [0.5] * len(ords)
    return [(d - lo) / (hi - lo) for d in ords]


def write_output(run_dir: Path, payload: dict) -> Path:
    out_path = run_dir / "ranked_papers.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description="Rank arXiv papers by BM25 + recency")
    p.add_argument("--input-dir", type=Path, default=None,
                   help="Run directory containing raw_papers.json "
                        "(default: resolved from ./output/latest_run.txt)")
    p.add_argument("--relevance-weight", type=float, default=0.80,
                   help="Weight on relevance_score_normalized (default 0.80)")
    p.add_argument("--recency-weight", type=float, default=0.20,
                   help="Weight on recency_score (default 0.20)")
    args = p.parse_args()

    run_dir = resolve_input_dir(args.input_dir)
    raw = load_raw(run_dir)
    papers = raw["papers"]

    config = {
        "relevance_method": "bm25_okapi",
        "relevance_weight": args.relevance_weight,
        "recency_weight": args.recency_weight,
        "doc_fields": ["title", "abstract", "categories"],
        "query_fields": ["normalized_query", "search_terms"],
    }

    if not papers:
        payload = {
            "query": raw["query"],
            "fetched_at": raw.get("fetched_at"),
            "ranked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ranking_config": config,
            "count": 0,
            "papers": [],
        }
        out_path = write_output(run_dir, payload)
        print(f"WARN: empty corpus -> {out_path}", file=sys.stderr)
        return 0

    query_tokens = build_query_tokens(raw["query"])
    docs = [build_doc_tokens(p) for p in papers]
    # rank_bm25 errors on docs that are entirely empty; substitute a sentinel
    docs = [d if d else ["__empty__"] for d in docs]
    bm25 = BM25Okapi(docs)
    raw_relevance = [float(s) for s in bm25.get_scores(query_tokens)]
    rel_norm = minmax(raw_relevance)
    rec = recency_scores(papers)

    rw, cw = args.relevance_weight, args.recency_weight
    ranked = []
    for paper, r_raw, r_norm, r_rec in zip(papers, raw_relevance, rel_norm, rec):
        final = rw * r_norm + cw * r_rec
        ranked.append({
            **paper,
            "scores": {
                "relevance_score": round(r_raw, 6),
                "relevance_score_normalized": round(r_norm, 6),
                "recency_score": round(r_rec, 6),
                "final_score": round(final, 6),
            },
        })

    ranked.sort(key=lambda p: p["scores"]["final_score"], reverse=True)
    for i, paper in enumerate(ranked, 1):
        paper["rank"] = i

    payload = {
        "query": raw["query"],
        "fetched_at": raw.get("fetched_at"),
        "ranked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ranking_config": config,
        "count": len(ranked),
        "papers": ranked,
    }
    out_path = write_output(run_dir, payload)
    print(f"INFO: wrote {len(ranked)} ranked papers -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
