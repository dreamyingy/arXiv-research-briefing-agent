#!/usr/bin/env python3
"""paper-report: render daily arXiv briefing artifacts from pipeline JSON."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


EMPTY_EXTRACTION = {
    "main_contribution": "",
    "method": "",
    "task": "",
    "keywords": [],
    "datasets_or_domains": [],
    "evaluation_signals": [],
    "limitations": "",
    "evidence_sentences": {"contribution": "", "method": "", "task": ""},
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


def load_json(path: Path, required: bool = True) -> dict:
    if not path.exists():
        if required:
            sys.exit(f"ERROR: required input JSON not found: {path}")
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


def short_authors(authors: list[str], limit: int = 3) -> str:
    if not authors:
        return ""
    if len(authors) <= limit:
        return ", ".join(authors)
    return ", ".join(authors[:limit]) + " et al."


def md_escape(text: object) -> str:
    s = "" if text is None else str(text)
    return s.replace("|", "\\|").replace("\n", " ")


def fmt_score(value: object) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.3f}"
    return ""


def extraction_default() -> dict:
    return json.loads(json.dumps(EMPTY_EXTRACTION))


def paper_card(paper: dict, extraction: dict, graph_metrics: dict | None) -> dict:
    scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
    out = {
        "id": paper.get("id"),
        "rank": paper.get("rank"),
        "title": paper.get("title", ""),
        "authors": paper.get("authors", []),
        "published": paper.get("published"),
        "primary_category": paper.get("primary_category"),
        "categories": paper.get("categories", []),
        "url": paper.get("url"),
        "pdf_url": paper.get("pdf_url"),
        "scores": scores,
        "extraction": extraction or extraction_default(),
    }
    if graph_metrics is not None:
        out["graph_metrics"] = graph_metrics
    return out


def highlight_from_paper(paper: dict, reason: str) -> dict:
    return {
        "id": paper.get("id"),
        "rank": paper.get("rank"),
        "title": paper.get("title", ""),
        "url": paper.get("url"),
        "reason": reason,
    }


def pick_highlights(papers: list[dict], include_graph_metrics: bool) -> dict:
    if not papers:
        return {}

    highlights = {
        "most_relevant": highlight_from_paper(
            papers[0],
            f"Rank {papers[0].get('rank')} with final_score "
            f"{fmt_score(papers[0].get('scores', {}).get('final_score'))}.",
        )
    }
    if not include_graph_metrics:
        return highlights

    def metric_value(paper: dict, key: str) -> float:
        gm = paper.get("graph_metrics", {})
        val = gm.get(key, 0.0) if isinstance(gm, dict) else 0.0
        return float(val) if isinstance(val, (int, float)) else 0.0

    highest_pagerank = max(papers, key=lambda p: metric_value(p, "pagerank"))
    most_novel = max(papers, key=lambda p: metric_value(p, "novelty"))
    most_bridging = max(papers, key=lambda p: metric_value(p, "bridging_score"))

    highlights["highest_pagerank"] = highlight_from_paper(
        highest_pagerank,
        f"Highest PageRank among reported papers: "
        f"{fmt_score(metric_value(highest_pagerank, 'pagerank'))}.",
    )
    highlights["most_novel"] = highlight_from_paper(
        most_novel,
        f"Highest novelty score among reported papers: "
        f"{fmt_score(metric_value(most_novel, 'novelty'))}.",
    )
    highlights["most_bridging"] = highlight_from_paper(
        most_bridging,
        f"Highest bridging score among reported papers: "
        f"{fmt_score(metric_value(most_bridging, 'bridging_score'))}.",
    )
    return highlights


def render_markdown(report: dict) -> str:
    query = report.get("query") or {}
    counts = report.get("counts") or {}
    graph_summary = report.get("graph_summary") or {}
    papers = report.get("papers") or []
    highlights = report.get("highlights") or {}

    lines = [
        "# Daily arXiv Research Briefing",
        "",
        f"Generated at: `{report.get('generated_at')}`",
        "",
        "## Query",
        "",
        f"- Original: {query.get('original_query', '')}",
        f"- Normalized: `{query.get('normalized_query', '')}`",
        f"- Search terms: {', '.join(query.get('search_terms', []) or [])}",
        f"- Date range: {query.get('start_date', '')} to {query.get('end_date', '')}",
        f"- Categories: {', '.join(query.get('categories', []) or []) or 'all'}",
        "",
        "## Corpus",
        "",
        f"- Ranked papers: {counts.get('ranked_papers', 0)}",
        f"- Enriched papers: {counts.get('enriched_papers', 0)}",
        f"- Graph metric papers: {counts.get('graph_metric_papers', 0)}",
        f"- Reported papers: {counts.get('reported_papers', 0)}",
    ]

    if graph_summary:
        lines.extend([
            f"- Graph nodes: {graph_summary.get('node_count', 0)}",
            f"- Graph edges: {graph_summary.get('edge_count', 0)}",
            f"- Paper projection edges: {graph_summary.get('paper_projection_edge_count', 0)}",
        ])

    lines.extend(["", "## Highlights", ""])
    if not highlights:
        lines.append("- No highlights available.")
    else:
        for label, item in highlights.items():
            title = item.get("title", "")
            rank = item.get("rank", "")
            url = item.get("url", "")
            reason = item.get("reason", "")
            lines.append(f"- **{label.replace('_', ' ').title()}**: Rank {rank}, [{title}]({url}). {reason}")

    lines.extend([
        "",
        "## Top Papers",
        "",
        "| Rank | Title | Authors | Published | Category | Final | PageRank | Novelty | URL |",
        "|---:|---|---|---|---|---:|---:|---:|---|",
    ])

    for paper in papers:
        gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
        scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
        lines.append(
            "| {rank} | {title} | {authors} | {published} | {cat} | {final} | {pr} | {novelty} | [abs]({url}) |".format(
                rank=paper.get("rank", ""),
                title=md_escape(paper.get("title", "")),
                authors=md_escape(short_authors(paper.get("authors", []))),
                published=paper.get("published", ""),
                cat=paper.get("primary_category", ""),
                final=fmt_score(scores.get("final_score")),
                pr=fmt_score(gm.get("pagerank")),
                novelty=fmt_score(gm.get("novelty")),
                url=paper.get("url", ""),
            )
        )

    lines.extend(["", "## Paper Notes", ""])
    if not papers:
        lines.append("No papers to report.")

    for paper in papers:
        ext = paper.get("extraction", {}) if isinstance(paper.get("extraction"), dict) else {}
        gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
        scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
        evidence = ext.get("evidence_sentences", {}) if isinstance(ext.get("evidence_sentences"), dict) else {}
        lines.extend([
            f"### {paper.get('rank')}. {paper.get('title', '')}",
            "",
            f"- ID: `{paper.get('id')}`",
            f"- Authors: {short_authors(paper.get('authors', []), limit=6)}",
            f"- Published: {paper.get('published', '')}; Category: {paper.get('primary_category', '')}",
            f"- Scores: final={fmt_score(scores.get('final_score'))}, relevance={fmt_score(scores.get('relevance_score_normalized'))}, recency={fmt_score(scores.get('recency_score'))}",
        ])
        if gm:
            lines.append(
                f"- Graph: PageRank={fmt_score(gm.get('pagerank'))}, novelty={fmt_score(gm.get('novelty'))}, bridging={fmt_score(gm.get('bridging_score'))}"
            )
        lines.extend([
            f"- Contribution: {ext.get('main_contribution', '')}",
            f"- Method: {ext.get('method', '')}",
            f"- Task: {ext.get('task', '')}",
            f"- Keywords: {', '.join(ext.get('keywords', []) or [])}",
            f"- Datasets/domains: {', '.join(ext.get('datasets_or_domains', []) or [])}",
            f"- Evaluation signals: {'; '.join(ext.get('evaluation_signals', []) or [])}",
            f"- Limitations: {ext.get('limitations', '')}",
            f"- Evidence: contribution=\"{evidence.get('contribution', '')}\" method=\"{evidence.get('method', '')}\" task=\"{evidence.get('task', '')}\"",
            f"- URL: {paper.get('url', '')}",
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"


def build_report(run_dir: Path, top_n: int, include_graph_metrics: bool) -> dict:
    ranked = load_json(run_dir / "ranked_papers.json")
    enriched = load_json(run_dir / "enriched_papers.json")
    graph_metrics = load_json(run_dir / "graph_metrics.json")
    graph = load_json(run_dir / "graph.json", required=False)

    ranked_papers = ranked.get("papers")
    if not isinstance(ranked_papers, list):
        sys.exit("ERROR: ranked_papers.json missing required list key 'papers'")

    enriched_by_id = index_by_id(enriched)
    graph_by_id = index_by_id(graph_metrics) if include_graph_metrics else {}
    selected = ranked_papers[:top_n]

    reported = []
    for paper in selected:
        pid = str(paper.get("id"))
        enriched_paper = enriched_by_id.get(pid)
        graph_paper = graph_by_id.get(pid)

        if enriched_paper is None:
            print(f"WARN: no extraction found for paper {pid}; using empty defaults", file=sys.stderr)
        extraction = (
            enriched_paper.get("extraction", extraction_default())
            if isinstance(enriched_paper, dict)
            else extraction_default()
        )

        if include_graph_metrics and graph_paper is None:
            print(f"WARN: no graph metrics found for paper {pid}; using empty metrics", file=sys.stderr)
        metrics = (
            graph_paper.get("graph_metrics", {})
            if include_graph_metrics and isinstance(graph_paper, dict)
            else ({} if include_graph_metrics else None)
        )
        reported.append(paper_card(paper, extraction, metrics))

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = {
        "query": ranked.get("query"),
        "generated_at": generated_at,
        "report_config": {
            "top_n": top_n,
            "include_graph_metrics": include_graph_metrics,
            "method": "json_join_markdown_v1",
        },
        "source_files": {
            "ranked_papers": "ranked_papers.json",
            "enriched_papers": "enriched_papers.json",
            "graph_metrics": "graph_metrics.json",
            "graph": "graph.json" if graph else None,
        },
        "counts": {
            "ranked_papers": len(ranked_papers),
            "enriched_papers": len(enriched.get("papers", []) or []),
            "graph_metric_papers": len(graph_metrics.get("papers", []) or []),
            "reported_papers": len(reported),
        },
        "highlights": pick_highlights(reported, include_graph_metrics),
        "graph_summary": graph.get("graph_summary", {}) if graph else {},
        "count": len(reported),
        "papers": reported,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Render arXiv briefing markdown and JSON")
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Run directory containing report inputs "
                             "(default: resolved from ./output/latest_run.txt)")
    parser.add_argument("--top-n", type=int, default=10,
                        help="Number of papers to include in briefing (default 10)")
    parser.add_argument("--no-graph-metrics", action="store_true",
                        help="Omit graph metrics from rendered report")
    args = parser.parse_args()

    if args.top_n <= 0:
        sys.exit(f"ERROR: --top-n must be positive, got {args.top_n}")

    run_dir = resolve_input_dir(args.input_dir)
    report = build_report(run_dir, args.top_n, include_graph_metrics=not args.no_graph_metrics)

    json_path = run_dir / "briefing.json"
    md_path = run_dir / "briefing.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")

    if report["count"] == 0:
        print(f"WARN: empty briefing -> {md_path}", file=sys.stderr)
    else:
        print(f"INFO: wrote briefing for {report['count']} papers -> {md_path}", file=sys.stderr)
    print(f"INFO: wrote structured briefing -> {json_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
