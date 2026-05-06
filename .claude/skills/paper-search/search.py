#!/usr/bin/env python3
"""paper-search: arXiv data entry for the daily briefing agent.

Reads a parsed query JSON, calls the arXiv API, writes cleaned raw_papers.json.
Caches by query hash so identical queries do not re-hit the network.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import arxiv
except ImportError:
    sys.exit("ERROR: 'arxiv' package not installed. Run: pip install arxiv")


ID_VERSION_RE = re.compile(r"v(\d+)$")
SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def derive_slug(search_terms: list[str], max_len: int = 30) -> str:
    joined = "-".join(search_terms[:3])
    s = SLUG_RE.sub("-", joined).strip("-").lower()
    return s[:max_len].rstrip("-") or "query"


def make_run_id(search_terms: list[str]) -> str:
    return f"{datetime.now().strftime('%Y-%m-%d_%H%M')}_{derive_slug(search_terms)}"


def load_query(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"ERROR: query file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        q = json.load(f)
    if not q.get("search_terms"):
        sys.exit("ERROR: query.search_terms must be a non-empty list.")
    return q


def apply_defaults(q: dict) -> dict:
    today = datetime.now(timezone.utc).date()
    q.setdefault("end_date", today.isoformat())
    q.setdefault("start_date", (today - timedelta(days=30)).isoformat())
    q.setdefault("max_results", 50)
    q.setdefault("categories", [])
    for k in ("start_date", "end_date"):
        try:
            datetime.strptime(q[k], "%Y-%m-%d")
        except ValueError:
            sys.exit(f"ERROR: {k} must be YYYY-MM-DD, got {q[k]!r}")
    return q


def cache_key(q: dict) -> str:
    keyed = {
        "search_terms": sorted(q["search_terms"]),
        "start_date": q["start_date"],
        "end_date": q["end_date"],
        "categories": sorted(q["categories"]),
        "max_results": q["max_results"],
    }
    blob = json.dumps(keyed, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def build_arxiv_query(q: dict) -> str:
    term_clause = " AND ".join(f'all:"{t}"' for t in q["search_terms"])
    parts = [f"({term_clause})"]
    if q["categories"]:
        cat_clause = " OR ".join(f"cat:{c}" for c in q["categories"])
        parts.append(f"({cat_clause})")
    start = q["start_date"].replace("-", "") + "0000"
    end = q["end_date"].replace("-", "") + "2359"
    parts.append(f"submittedDate:[{start} TO {end}]")
    return " AND ".join(parts)


def split_arxiv_id(entry_id: str) -> tuple[str, str]:
    short = entry_id.rsplit("/abs/", 1)[-1]
    m = ID_VERSION_RE.search(short)
    if m:
        return short[: m.start()], short[m.start():]
    return short, ""


def clean_result(r) -> dict | None:
    try:
        base_id, version = split_arxiv_id(r.entry_id)
        return {
            "id": base_id,
            "version": version,
            "title": " ".join(r.title.split()),
            "abstract": " ".join(r.summary.split()),
            "authors": [a.name for a in r.authors],
            "primary_category": r.primary_category,
            "categories": list(r.categories),
            "published": r.published.date().isoformat(),
            "updated": r.updated.date().isoformat(),
            "url": f"https://arxiv.org/abs/{base_id}",
            "pdf_url": r.pdf_url,
            "doi": r.doi,
            "journal_ref": r.journal_ref,
            "comment": r.comment,
        }
    except Exception as e:
        print(
            f"WARN: failed to parse entry {getattr(r, 'entry_id', '?')}: {e}",
            file=sys.stderr,
        )
        return None


def fetch_papers(q: dict) -> list[dict]:
    client = arxiv.Client(
        page_size=min(q["max_results"], 100),
        delay_seconds=3,
        num_retries=3,
    )
    search = arxiv.Search(
        query=build_arxiv_query(q),
        max_results=q["max_results"],
        sort_by=arxiv.SortCriterion.SubmittedDate,
        sort_order=arxiv.SortOrder.Descending,
    )
    seen: set[str] = set()
    papers: list[dict] = []
    for r in client.results(search):
        cleaned = clean_result(r)
        if cleaned is None or cleaned["id"] in seen:
            continue
        seen.add(cleaned["id"])
        papers.append(cleaned)
    return papers


def main() -> int:
    p = argparse.ArgumentParser(description="arXiv search for the daily briefing agent")
    p.add_argument("--query-file", required=True, type=Path,
                   help="Path to the parsed query JSON")
    p.add_argument("--output-dir", type=Path, default=None,
                   help="Output directory (default: ./output/<YYYY-MM-DD_HHMM_slug>/)")
    p.add_argument("--no-cache", action="store_true",
                   help="Bypass cache and force a fresh arXiv request")
    args = p.parse_args()

    q = apply_defaults(load_query(args.query_file))

    if args.output_dir is None:
        output_root = Path("output")
        run_id = make_run_id(q["search_terms"])
        output_dir = output_root / run_id
        cache_dir = output_root / "cache"
        update_latest = True
    else:
        output_root = None
        run_id = args.output_dir.name
        output_dir = args.output_dir
        cache_dir = output_dir / "cache"
        update_latest = False

    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{cache_key(q)}.json"
    out_path = output_dir / "raw_papers.json"

    def finalize_run() -> None:
        (output_dir / "query.json").write_text(
            json.dumps(q, ensure_ascii=False, indent=2), encoding="utf-8")
        if update_latest:
            (output_root / "latest_run.txt").write_text(run_id, encoding="utf-8")

    if cache_path.exists() and not args.no_cache:
        print(f"INFO: cache hit -> {cache_path}", file=sys.stderr)
        shutil.copyfile(cache_path, out_path)
        finalize_run()
        return 0

    try:
        papers = fetch_papers(q)
    except Exception as e:
        sys.exit(f"ERROR: arXiv request failed: {e}")

    payload = {
        "query": q,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(papers),
        "papers": papers,
    }
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    out_path.write_text(body, encoding="utf-8")
    cache_path.write_text(body, encoding="utf-8")
    finalize_run()
    print(f"INFO: wrote {len(papers)} papers -> {out_path}", file=sys.stderr)
    if not papers:
        print("WARN: zero results - check search_terms / date range / categories",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
