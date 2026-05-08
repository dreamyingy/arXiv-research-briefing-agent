#!/usr/bin/env python3
"""follow-up: grounded Q&A over cached arXiv briefing artifacts."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ID_RE = re.compile(r"\b\d{4}\.\d{4,5}\b")
RANK_RE = re.compile(r"(?:rank|#|第)\s*(\d+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "about", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "me", "more", "of", "on", "or", "paper",
    "papers", "rank", "related", "show", "tell", "the", "this", "to", "vs",
    "what", "which", "with", "compare", "most", "are", "about", "please",
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
            "Run upstream skills first, or pass --input-dir."
        )
    run_id = latest.read_text(encoding="utf-8").strip()
    run_dir = Path("output") / run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: run directory referenced by latest_run.txt is missing: {run_dir}")
    return run_dir


def load_json(path: Path, required: bool = False) -> dict:
    if not path.exists():
        if required:
            sys.exit(f"ERROR: required JSON not found: {path}. Run paper-report first.")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: {path.name} is not valid JSON: {e}")


def index_by_id(data: dict) -> dict[str, dict]:
    papers = data.get("papers", [])
    if not isinstance(papers, list):
        return {}
    return {str(p.get("id")): p for p in papers if p.get("id")}


def merge_paper(base: dict, *overlays: dict | None) -> dict:
    merged = dict(base)
    for overlay in overlays:
        if not isinstance(overlay, dict):
            continue
        for key, value in overlay.items():
            if key in {"extraction", "graph_metrics", "scores"} and isinstance(value, dict):
                existing = merged.get(key, {})
                if isinstance(existing, dict):
                    merged[key] = {**existing, **value}
                else:
                    merged[key] = value
            elif key not in merged or merged.get(key) in (None, "", [], {}):
                merged[key] = value
    return merged


def load_context(run_dir: Path) -> tuple[dict, list[dict]]:
    briefing = load_json(run_dir / "briefing.json", required=True)
    ranked = load_json(run_dir / "ranked_papers.json")
    enriched = load_json(run_dir / "enriched_papers.json")
    graph_metrics = load_json(run_dir / "graph_metrics.json")

    ranked_by_id = index_by_id(ranked)
    briefing_by_id = index_by_id(briefing)
    enriched_by_id = index_by_id(enriched)
    graph_by_id = index_by_id(graph_metrics)

    ids: list[str] = []
    for source in (ranked_by_id, briefing_by_id, enriched_by_id, graph_by_id):
        for pid in source:
            if pid not in ids:
                ids.append(pid)

    papers = [
        merge_paper(
            ranked_by_id.get(pid, {}),
            briefing_by_id.get(pid),
            enriched_by_id.get(pid),
            graph_by_id.get(pid),
        )
        for pid in ids
    ]
    papers.sort(key=lambda p: int(p.get("rank") or 10**9))
    return briefing, papers


def question_text(args: argparse.Namespace) -> str:
    parts = []
    if args.question:
        parts.append(args.question)
    if args.question_args:
        parts.extend(args.question_args)
    if parts:
        return " ".join(parts).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def fmt_score(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return "unavailable"


def short_authors(authors: list[str], limit: int = 4) -> str:
    if not authors:
        return "unavailable"
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + " et al."


def paper_label(paper: dict) -> str:
    return f"Rank {paper.get('rank', '?')} `{paper.get('id', '?')}`: {paper.get('title', '')}"


def available_identifiers(papers: list[dict], top_k: int = 5) -> str:
    lines = ["Available top papers:"]
    for paper in papers[:top_k]:
        lines.append(f"- {paper_label(paper)}")
    return "\n".join(lines)


def find_paper_by_rank(papers: list[dict], rank: int) -> dict | None:
    for paper in papers:
        if int(paper.get("rank") or -1) == rank:
            return paper
    return None


def find_paper_by_id(papers: list[dict], paper_id: str) -> dict | None:
    for paper in papers:
        if str(paper.get("id")) == paper_id:
            return paper
    return None


def find_paper_by_title_fragment(papers: list[dict], question: str) -> dict | None:
    q = question.lower()
    candidates = []
    for paper in papers:
        title = str(paper.get("title", "")).lower()
        if title and title in q:
            candidates.append((len(title), paper))
        elif title:
            words = [w for w in TOKEN_RE.findall(title) if w not in STOPWORDS and len(w) > 3]
            overlap = sum(1 for w in words if w in q)
            if overlap >= 2:
                candidates.append((overlap, paper))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def identify_papers(question: str, papers: list[dict]) -> list[dict]:
    found: list[dict] = []
    seen: set[str] = set()

    for paper_id in ID_RE.findall(question):
        paper = find_paper_by_id(papers, paper_id)
        if paper and paper_id not in seen:
            found.append(paper)
            seen.add(paper_id)

    for rank_s in RANK_RE.findall(question):
        paper = find_paper_by_rank(papers, int(rank_s))
        if paper and str(paper.get("id")) not in seen:
            found.append(paper)
            seen.add(str(paper.get("id")))

    title_match = find_paper_by_title_fragment(papers, question)
    if title_match and str(title_match.get("id")) not in seen:
        found.append(title_match)

    return found


def extraction(paper: dict) -> dict:
    ext = paper.get("extraction", {})
    return ext if isinstance(ext, dict) else {}


def graph_metrics(paper: dict) -> dict:
    gm = paper.get("graph_metrics", {})
    return gm if isinstance(gm, dict) else {}


def scores(paper: dict) -> dict:
    sc = paper.get("scores", {})
    return sc if isinstance(sc, dict) else {}


def answer_detail(paper: dict) -> str:
    ext = extraction(paper)
    gm = graph_metrics(paper)
    sc = scores(paper)
    evidence = ext.get("evidence_sentences", {}) if isinstance(ext.get("evidence_sentences"), dict) else {}
    lines = [
        f"## {paper_label(paper)}",
        "",
        f"- Authors: {short_authors(paper.get('authors', []), limit=8)}",
        f"- Published: {paper.get('published', 'unavailable')}; category: {paper.get('primary_category', 'unavailable')}",
        f"- URL: {paper.get('url', 'unavailable')}",
        f"- Scores: final={fmt_score(sc.get('final_score'))}, relevance={fmt_score(sc.get('relevance_score_normalized'))}, recency={fmt_score(sc.get('recency_score'))}",
        f"- Graph: PageRank={fmt_score(gm.get('pagerank'))}, novelty={fmt_score(gm.get('novelty'))}, bridging={fmt_score(gm.get('bridging_score'))}",
        "",
        "**Cached extraction**",
        "",
        f"- Contribution: {ext.get('main_contribution') or 'unavailable in cached extraction'}",
        f"- Method: {ext.get('method') or 'unavailable in cached extraction'}",
        f"- Task: {ext.get('task') or 'unavailable in cached extraction'}",
        f"- Keywords: {', '.join(ext.get('keywords', []) or []) or 'unavailable'}",
        f"- Datasets/domains: {', '.join(ext.get('datasets_or_domains', []) or []) or 'unavailable'}",
        f"- Evaluation signals: {'; '.join(ext.get('evaluation_signals', []) or []) or 'unavailable'}",
        f"- Limitations: {ext.get('limitations') or 'not found in cached abstract extraction'}",
        "",
        "**Evidence sentences**",
        "",
        f"- Contribution evidence: {evidence.get('contribution') or 'unavailable'}",
        f"- Method evidence: {evidence.get('method') or 'unavailable'}",
        f"- Task evidence: {evidence.get('task') or 'unavailable'}",
    ]
    return "\n".join(lines)


def answer_compare(papers: list[dict]) -> str:
    left, right = papers[0], papers[1]
    rows = [
        ("ID", left.get("id", ""), right.get("id", "")),
        ("Rank", left.get("rank", ""), right.get("rank", "")),
        ("Title", left.get("title", ""), right.get("title", "")),
        ("Final score", fmt_score(scores(left).get("final_score")), fmt_score(scores(right).get("final_score"))),
        ("PageRank", fmt_score(graph_metrics(left).get("pagerank")), fmt_score(graph_metrics(right).get("pagerank"))),
        ("Novelty", fmt_score(graph_metrics(left).get("novelty")), fmt_score(graph_metrics(right).get("novelty"))),
        ("Contribution", extraction(left).get("main_contribution", ""), extraction(right).get("main_contribution", "")),
        ("Method", extraction(left).get("method", ""), extraction(right).get("method", "")),
        ("Task", extraction(left).get("task", ""), extraction(right).get("task", "")),
        ("Keywords", ", ".join(extraction(left).get("keywords", []) or []), ", ".join(extraction(right).get("keywords", []) or [])),
    ]
    lines = [
        f"## Comparison: `{left.get('id')}` vs `{right.get('id')}`",
        "",
        "| Field | Paper A | Paper B |",
        "|---|---|---|",
    ]
    for field, a, b in rows:
        lines.append(f"| {field} | {str(a).replace('|', '/')} | {str(b).replace('|', '/')} |")
    lines.extend([
        "",
        "Grounding: all fields above are copied from cached ranking, extraction, and graph metric JSON files.",
    ])
    return "\n".join(lines)


def token_terms(question: str) -> list[str]:
    return [
        t for t in TOKEN_RE.findall(question.lower())
        if t not in STOPWORDS and len(t) > 1 and not t.isdigit()
    ]


def searchable_text(paper: dict) -> str:
    ext = extraction(paper)
    fields = [
        paper.get("title", ""),
        paper.get("abstract", ""),
        " ".join(paper.get("categories", []) or []),
        ext.get("main_contribution", ""),
        ext.get("method", ""),
        ext.get("task", ""),
        " ".join(ext.get("keywords", []) or []),
        " ".join(ext.get("datasets_or_domains", []) or []),
    ]
    return " ".join(str(f) for f in fields).lower()


def answer_search(question: str, papers: list[dict], top_k: int) -> str:
    terms = token_terms(question)
    if not terms:
        return answer_top(papers, top_k)

    scored = []
    for paper in papers:
        text = searchable_text(paper)
        score = sum(text.count(term) for term in terms)
        if score:
            final = scores(paper).get("final_score", 0.0)
            score += float(final) if isinstance(final, (int, float)) else 0.0
            scored.append((score, paper))

    if not scored:
        return (
            "I could not find cached papers matching those terms.\n\n"
            + available_identifiers(papers, top_k)
        )

    scored.sort(key=lambda x: (-x[0], int(x[1].get("rank") or 10**9)))
    lines = [
        "## Matching Cached Papers",
        "",
        f"Search terms used from your question: {', '.join(terms)}",
        "",
    ]
    for _, paper in scored[:top_k]:
        ext = extraction(paper)
        lines.append(
            f"- {paper_label(paper)} | final={fmt_score(scores(paper).get('final_score'))} | "
            f"keywords={', '.join(ext.get('keywords', [])[:5] if ext.get('keywords') else [])} | {paper.get('url', '')}"
        )
    return "\n".join(lines)


def answer_network(question: str, papers: list[dict], top_k: int) -> str:
    q = question.lower()
    if "novel" in q or "新颖" in q:
        key = "novelty"
        title = "Most Novel Cached Papers"
    elif "bridg" in q or "桥接" in q:
        key = "bridging_score"
        title = "Highest Bridging Cached Papers"
    else:
        key = "pagerank"
        title = "Most Central Cached Papers"

    ranked = sorted(
        papers,
        key=lambda p: float(graph_metrics(p).get(key, 0.0) or 0.0),
        reverse=True,
    )
    lines = [f"## {title}", ""]
    for paper in ranked[:top_k]:
        gm = graph_metrics(paper)
        metric_parts = [f"{key}={fmt_score(gm.get(key))}"]
        if key != "pagerank":
            metric_parts.append(f"PageRank={fmt_score(gm.get('pagerank'))}")
        if key != "novelty":
            metric_parts.append(f"novelty={fmt_score(gm.get('novelty'))}")
        if key != "bridging_score":
            metric_parts.append(f"bridging={fmt_score(gm.get('bridging_score'))}")
        lines.append(f"- {paper_label(paper)} | {' | '.join(metric_parts)} | {paper.get('url', '')}")
    return "\n".join(lines)


def answer_top(papers: list[dict], top_k: int) -> str:
    lines = ["## Top Ranked Cached Papers", ""]
    for paper in papers[:top_k]:
        lines.append(
            f"- {paper_label(paper)} | final={fmt_score(scores(paper).get('final_score'))} | {paper.get('url', '')}"
        )
    return "\n".join(lines)


def answer_question(question: str, briefing: dict, papers: list[dict], top_k: int) -> str:
    q = question.lower()
    identified = identify_papers(question, papers)

    if ("compare" in q or " vs " in q or "对比" in q or "比较" in q) and len(identified) >= 2:
        return answer_compare(identified[:2])

    if ("compare" in q or " vs " in q or "对比" in q or "比较" in q) and len(identified) < 2:
        return (
            "I need two cached paper identifiers to compare. Use ranks or arXiv IDs.\n\n"
            + available_identifiers(papers, top_k)
        )

    if identified:
        return answer_detail(identified[0])

    if any(term in q for term in ["novel", "bridg", "central", "pagerank", "新颖", "桥接", "中心"]):
        return answer_network(question, papers, top_k)

    if any(term in q for term in ["top", "rank", "best", "relevant", "相关", "最"]):
        return answer_search(question, papers, top_k)

    if "summary" in q or "overview" in q or "总结" in q or "概览" in q:
        query = briefing.get("query", {}) if isinstance(briefing.get("query"), dict) else {}
        return (
            "## Briefing Overview\n\n"
            f"- Query: {query.get('original_query') or query.get('normalized_query') or 'unavailable'}\n"
            f"- Reported papers: {briefing.get('count', 'unavailable')}\n\n"
            + answer_top(papers, top_k)
        )

    return (
        "I can answer from the cached run, but the question is ambiguous.\n\n"
        + answer_top(papers, top_k)
        + "\n\nTry asking about a stable identifier, for example `tell me more about rank 1` "
          "or `compare rank 1 and rank 3`."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Answer grounded follow-up questions")
    parser.add_argument("question_args", nargs="*",
                        help="Question text. If omitted, use --question or stdin.")
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Run directory containing briefing.json "
                             "(default: resolved from ./output/latest_run.txt)")
    parser.add_argument("--question", default=None,
                        help="Question text")
    parser.add_argument("--top-k", type=int, default=5,
                        help="Number of papers for list/search answers (default 5)")
    parser.add_argument("--save", action="store_true",
                        help="Append question and answer to followups.jsonl")
    args = parser.parse_args()

    if args.top_k <= 0:
        sys.exit(f"ERROR: --top-k must be positive, got {args.top_k}")

    question = question_text(args)
    if not question:
        sys.exit("ERROR: no follow-up question provided")

    run_dir = resolve_input_dir(args.input_dir)
    briefing, papers = load_context(run_dir)
    if not papers:
        answer = "No cached papers are available in this run. Run the upstream pipeline first."
    else:
        answer = answer_question(question, briefing, papers, args.top_k)

    print(answer)

    if args.save:
        record = {
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "question": question,
            "answer": answer,
        }
        with (run_dir / "followups.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
