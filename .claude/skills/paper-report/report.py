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

RECOMMENDATION_WEIGHTS = {
    "rank_final_score": 0.55,
    "network_value_score": 0.20,
    "novelty": 0.15,
    "bridging_score_normalized": 0.10,
}

PORTFOLIO_BONUSES = {
    "new_community": 0.06,
    "new_topic": 0.02,
    "max_new_topic_bonus": 0.06,
    "bridge_role": 0.04,
    "redundancy_penalty": 0.08,
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


def metric_float(metrics: dict, key: str) -> float:
    val = metrics.get(key, 0.0) if isinstance(metrics, dict) else 0.0
    return float(val) if isinstance(val, (int, float)) else 0.0


def score_float(scores: dict, key: str) -> float:
    val = scores.get(key, 0.0) if isinstance(scores, dict) else 0.0
    return float(val) if isinstance(val, (int, float)) else 0.0


def normalize_map(values: dict[str, float], default: float = 0.0) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {key: default for key in values}
    return {key: (value - lo) / (hi - lo) for key, value in values.items()}


def recommendation_for_paper(paper: dict, extraction: dict, graph_metrics: dict | None) -> dict:
    scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
    metrics = graph_metrics if isinstance(graph_metrics, dict) else {}
    rank = int(paper.get("rank") or 10**9)
    final_score = scores.get("final_score", 0.0)
    relevance = scores.get("relevance_score_normalized", 0.0)
    novelty = metric_float(metrics, "novelty")
    pagerank = metric_float(metrics, "pagerank")
    bridging = metric_float(metrics, "bridging_score")
    network_value = metric_float(metrics, "network_value_score")
    role = metrics.get("network_role") or "ranked_candidate"

    evidence: list[str] = []
    best_for: list[str] = []
    caveats: list[str] = []

    if rank <= 3:
        evidence.append(f"top-{rank} ranked match for the query")
        best_for.append("starting the reading list")
    if isinstance(final_score, (int, float)) and final_score >= 0.75:
        evidence.append(f"strong final ranking score ({fmt_score(final_score)})")
    if isinstance(relevance, (int, float)) and relevance >= 0.75:
        evidence.append("strong query relevance")
        best_for.append("understanding the query's core topic")
    if pagerank > 0:
        evidence.append(f"network PageRank {fmt_score(pagerank)}")
    if novelty >= 0.70:
        evidence.append(f"high novelty signal ({fmt_score(novelty)})")
        best_for.append("finding less obvious ideas")
    if bridging >= 0.05 or "bridge" in str(role):
        evidence.append(f"bridging score {fmt_score(bridging)}")
        best_for.append("connecting adjacent research threads")
    if network_value >= 0.65:
        evidence.append(f"high network value score ({fmt_score(network_value)})")

    keywords = extraction.get("keywords", []) if isinstance(extraction, dict) else []
    datasets = extraction.get("datasets_or_domains", []) if isinstance(extraction, dict) else []
    evals = extraction.get("evaluation_signals", []) if isinstance(extraction, dict) else []
    if keywords:
        evidence.append("extracted keywords: " + ", ".join(str(k) for k in keywords[:4]))
    if datasets:
        best_for.append("mapping datasets or application domains")
    if evals:
        best_for.append("checking experimental evidence")
    else:
        caveats.append("evaluation details were not found in cached extraction")
    if not extraction.get("main_contribution"):
        caveats.append("main contribution is unavailable in cached extraction")
    if not extraction.get("limitations"):
        caveats.append("limitations were not detected from the cached text")

    if "core" in str(role) or pagerank >= 0.08:
        label = "Core"
    elif "bridge" in str(role):
        label = "Bridge"
    elif novelty >= 0.75:
        label = "Novel"
    elif rank <= 5:
        label = "Relevant"
    else:
        label = "Context"

    if rank <= 3 or label in {"Core", "Bridge"} or network_value >= 0.75:
        priority = "must-read"
    elif rank <= 10 or novelty >= 0.65:
        priority = "skim"
    else:
        priority = "optional"

    if not best_for:
        best_for.append("background scanning")
    if not evidence:
        evidence.append("included by upstream ranking")

    role_text = str(role).replace("_", " ")
    why = (
        f"Recommended as a {label.lower()} paper because it is {role_text} "
        f"and has {evidence[0]}."
    )

    return {
        "label": label,
        "read_priority": priority,
        "network_role": role,
        "why_recommended": why,
        "best_for": list(dict.fromkeys(best_for)),
        "evidence": evidence,
        "caveats": caveats,
    }


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
        "recommendation": recommendation_for_paper(
            paper, extraction or extraction_default(), graph_metrics
        ),
    }
    if graph_metrics is not None:
        out["graph_metrics"] = graph_metrics
    return out


def paper_id(paper: dict) -> str:
    return str(paper.get("id", ""))


def paper_topics(paper: dict) -> set[str]:
    ext = paper.get("extraction", {}) if isinstance(paper.get("extraction"), dict) else {}
    topics = set()
    for key in ("keywords", "datasets_or_domains"):
        values = ext.get(key, [])
        if isinstance(values, list):
            topics.update(str(value).strip().lower() for value in values if str(value).strip())
    for category in paper.get("categories", []) or []:
        if str(category).strip():
            topics.add(str(category).strip().lower())
    return topics


def paper_community(paper: dict) -> str:
    gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
    community_id = gm.get("community_id")
    if community_id is not None:
        return f"community:{community_id}"
    return f"category:{paper.get('primary_category') or 'unknown'}"


def max_neighbor_weight(papers: list[dict]) -> float:
    weights = []
    for paper in papers:
        gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
        for item in gm.get("nearest_neighbors", []) or []:
            weight = item.get("weight")
            if isinstance(weight, (int, float)):
                weights.append(float(weight))
    return max(weights) if weights else 1.0


def similarity_to_selected(paper: dict, selected_ids: set[str], max_weight: float) -> float:
    if not selected_ids:
        return 0.0
    gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
    strongest = 0.0
    for item in gm.get("nearest_neighbors", []) or []:
        if str(item.get("id")) in selected_ids and isinstance(item.get("weight"), (int, float)):
            strongest = max(strongest, float(item["weight"]))
    return min(strongest / max(max_weight, 1e-9), 1.0)


def base_recommendation_components(papers: list[dict]) -> dict[str, dict]:
    bridging_norm = normalize_map({
        paper_id(paper): metric_float(
            paper.get("graph_metrics", {})
            if isinstance(paper.get("graph_metrics"), dict)
            else {},
            "bridging_score",
        )
        for paper in papers
    })
    components = {}
    for paper in papers:
        pid = paper_id(paper)
        scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
        gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
        parts = {
            "rank_final_score": score_float(scores, "final_score"),
            "network_value_score": metric_float(gm, "network_value_score"),
            "novelty": metric_float(gm, "novelty"),
            "bridging_score_normalized": bridging_norm.get(pid, 0.0),
        }
        base = sum(RECOMMENDATION_WEIGHTS[key] * parts[key] for key in RECOMMENDATION_WEIGHTS)
        components[pid] = {
            "base_score": base,
            "components": parts,
        }
    return components


def selection_reason(paper: dict, diversity_bonus: float, bridge_bonus: float,
                     redundancy_penalty: float) -> str:
    rec = paper.get("recommendation", {}) if isinstance(paper.get("recommendation"), dict) else {}
    gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
    reasons = []
    if rec.get("why_recommended"):
        reasons.append(rec["why_recommended"])
    if diversity_bonus > 0:
        reasons.append(f"adds portfolio diversity (+{diversity_bonus:.3f})")
    if bridge_bonus > 0:
        reasons.append(f"adds bridge value (+{bridge_bonus:.3f})")
    if redundancy_penalty > 0:
        reasons.append(f"has overlap with already selected papers (-{redundancy_penalty:.3f})")
    if gm.get("network_role"):
        reasons.append(f"network role: {str(gm['network_role']).replace('_', ' ')}")
    return " ".join(reasons) if reasons else "Selected by portfolio recommendation score."


def select_recommendation_portfolio(papers: list[dict], top_n: int) -> list[dict]:
    if not papers:
        return []

    base_by_id = base_recommendation_components(papers)
    selected: list[dict] = []
    selected_ids: set[str] = set()
    selected_communities: set[str] = set()
    selected_topics: set[str] = set()
    remaining = list(papers)
    max_weight = max_neighbor_weight(papers)

    for rec_rank in range(1, min(top_n, len(remaining)) + 1):
        best_idx = 0
        best_score = float("-inf")
        best_extras: dict[str, float] = {}

        for idx, paper in enumerate(remaining):
            pid = paper_id(paper)
            base = base_by_id[pid]["base_score"]
            community = paper_community(paper)
            topics = paper_topics(paper)
            new_topics = topics - selected_topics
            new_community_bonus = (
                PORTFOLIO_BONUSES["new_community"]
                if community and community not in selected_communities
                else 0.0
            )
            new_topic_bonus = min(
                PORTFOLIO_BONUSES["max_new_topic_bonus"],
                PORTFOLIO_BONUSES["new_topic"] * len(new_topics),
            )
            role = str(
                (paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {})
                .get("network_role", "")
            )
            bridge_bonus = PORTFOLIO_BONUSES["bridge_role"] if "bridge" in role else 0.0
            redundancy = (
                PORTFOLIO_BONUSES["redundancy_penalty"]
                * similarity_to_selected(paper, selected_ids, max_weight)
            )
            adjusted = base + new_community_bonus + new_topic_bonus + bridge_bonus - redundancy
            if adjusted > best_score:
                best_score = adjusted
                best_idx = idx
                best_extras = {
                    "diversity_bonus": round(new_community_bonus + new_topic_bonus, 6),
                    "bridge_bonus": round(bridge_bonus, 6),
                    "redundancy_penalty": round(redundancy, 6),
                    "new_topic_count": len(new_topics),
                }

        chosen = remaining.pop(best_idx)
        pid = paper_id(chosen)
        selected_ids.add(pid)
        selected_communities.add(paper_community(chosen))
        selected_topics.update(paper_topics(chosen))

        rec_score = {
            "method": "portfolio_network_recommendation_v1",
            "score": round(best_score, 6),
            "base_score": round(base_by_id[pid]["base_score"], 6),
            "components": {
                key: round(value, 6)
                for key, value in base_by_id[pid]["components"].items()
            },
            "weights": RECOMMENDATION_WEIGHTS,
            "portfolio_adjustments": best_extras,
        }
        chosen["recommendation_rank"] = rec_rank
        chosen["recommendation_score"] = rec_score
        rec = chosen.get("recommendation", {}) if isinstance(chosen.get("recommendation"), dict) else {}
        rec["selection_reason"] = selection_reason(
            chosen,
            best_extras.get("diversity_bonus", 0.0),
            best_extras.get("bridge_bonus", 0.0),
            best_extras.get("redundancy_penalty", 0.0),
        )
        rec.setdefault("evidence", [])
        rec["evidence"] = list(dict.fromkeys(
            rec["evidence"]
            + [
                f"portfolio recommendation score {fmt_score(rec_score['score'])}",
                f"base network-aware score {fmt_score(rec_score['base_score'])}",
            ]
        ))
        chosen["recommendation"] = rec
        selected.append(chosen)

    return selected


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

    most_relevant = max(
        papers,
        key=lambda p: score_float(
            p.get("scores", {}) if isinstance(p.get("scores"), dict) else {},
            "final_score",
        ),
    )
    best_recommendation = min(papers, key=lambda p: int(p.get("recommendation_rank") or 10**9))
    highlights = {
        "most_relevant": highlight_from_paper(
            most_relevant,
            f"Search rank {most_relevant.get('rank')} with final_score "
            f"{fmt_score(most_relevant.get('scores', {}).get('final_score'))}.",
        ),
        "top_recommendation": highlight_from_paper(
            best_recommendation,
            f"Recommendation rank {best_recommendation.get('recommendation_rank')} with "
            f"portfolio score {fmt_score(best_recommendation.get('recommendation_score', {}).get('score'))}.",
        ),
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
    best_network_value = max(papers, key=lambda p: metric_value(p, "network_value_score"))

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
    highlights["highest_network_value"] = highlight_from_paper(
        best_network_value,
        f"Best combined network value score among reported papers: "
        f"{fmt_score(metric_value(best_network_value, 'network_value_score'))}.",
    )
    return highlights


def research_map_from_graph(graph: dict) -> dict:
    communities = graph.get("communities", []) if isinstance(graph, dict) else []
    social = graph.get("social_summary", {}) if isinstance(graph, dict) else {}
    return {
        "communities": communities[:8] if isinstance(communities, list) else [],
        "social_summary": social if isinstance(social, dict) else {},
    }


def visualizations_from_graph(run_dir: Path, graph: dict) -> list[dict]:
    rows = graph.get("visualizations", []) if isinstance(graph, dict) else []
    visualizations = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path or "/" in path or path.startswith("."):
            continue
        artifact = run_dir / path
        if not artifact.exists():
            continue
        visualizations.append({
            "name": item.get("name") or artifact.stem,
            "path": path,
            "description": item.get("description") or artifact.stem.replace("_", " "),
            "format": item.get("format") or artifact.suffix.lstrip("."),
            "node_count": item.get("node_count"),
            "edge_count": item.get("edge_count"),
        })
    return visualizations


def render_markdown(report: dict) -> str:
    query = report.get("query") or {}
    counts = report.get("counts") or {}
    graph_summary = report.get("graph_summary") or {}
    research_map = report.get("research_map") or {}
    visualizations = report.get("visualizations") or []
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
            f"- Communities: {graph_summary.get('community_count', 0)}",
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

    communities = research_map.get("communities", []) if isinstance(research_map, dict) else []
    social = research_map.get("social_summary", {}) if isinstance(research_map, dict) else {}
    lines.extend(["", "## Research Map", ""])
    if communities:
        for community in communities[:6]:
            reps = ", ".join(f"`{pid}`" for pid in community.get("representative_papers", [])[:3])
            topics = ", ".join(item.get("label", "") for item in community.get("top_topics", [])[:4])
            lines.append(
                f"- Community {community.get('community_id')}: **{community.get('label', '')}** "
                f"({community.get('size', 0)} papers). Representative papers: {reps or 'unavailable'}. "
                f"Topics: {topics or 'unavailable'}."
            )
    else:
        lines.append("- No community map available.")

    influential = social.get("influential_authors", []) if isinstance(social, dict) else []
    if influential:
        lines.extend(["", "Social signals:"])
        for author in influential[:5]:
            lines.append(
                f"- {author.get('name')}: {author.get('paper_count')} papers, "
                f"{author.get('community_count')} communities, topics={', '.join(author.get('top_topics', [])[:4])}"
            )

    if visualizations:
        lines.extend(["", "## Network Visualizations", ""])
        for viz in visualizations:
            description = viz.get("description") or viz.get("name") or "Network visualization"
            path = viz.get("path", "")
            node_count = viz.get("node_count")
            edge_count = viz.get("edge_count")
            size_note = ""
            if node_count is not None and edge_count is not None:
                size_note = f" ({node_count} nodes, {edge_count} edges)"
            lines.extend([
                f"### {description}{size_note}",
                "",
                f"![{md_escape(description)}]({path})",
                "",
            ])

    lines.extend([
        "",
        "## Recommended Reading Portfolio",
        "",
        "| Rec Rank | Search Rank | Title | Authors | Published | Category | Final | Rec Score | PageRank | Novelty | URL |",
        "|---:|---:|---|---|---|---|---:|---:|---:|---:|---|",
    ])

    for paper in papers:
        gm = paper.get("graph_metrics", {}) if isinstance(paper.get("graph_metrics"), dict) else {}
        scores = paper.get("scores", {}) if isinstance(paper.get("scores"), dict) else {}
        rec = paper.get("recommendation", {}) if isinstance(paper.get("recommendation"), dict) else {}
        rec_score = paper.get("recommendation_score", {}) if isinstance(paper.get("recommendation_score"), dict) else {}
        lines.append(
            "| {rec_rank} | {rank} | {title} | {authors} | {published} | {cat} | {final} | {rec_score} | {pr} | {novelty} | [abs]({url}) |".format(
                rec_rank=paper.get("recommendation_rank", ""),
                rank=paper.get("rank", ""),
                title=md_escape(f"{paper.get('title', '')} ({rec.get('label', 'Context')})"),
                authors=md_escape(short_authors(paper.get("authors", []))),
                published=paper.get("published", ""),
                cat=paper.get("primary_category", ""),
                final=fmt_score(scores.get("final_score")),
                rec_score=fmt_score(rec_score.get("score")),
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
        rec = paper.get("recommendation", {}) if isinstance(paper.get("recommendation"), dict) else {}
        rec_score = paper.get("recommendation_score", {}) if isinstance(paper.get("recommendation_score"), dict) else {}
        evidence = ext.get("evidence_sentences", {}) if isinstance(ext.get("evidence_sentences"), dict) else {}
        lines.extend([
            f"### Recommendation {paper.get('recommendation_rank')}: {paper.get('title', '')}",
            "",
            f"- ID: `{paper.get('id')}`",
            f"- Search rank: {paper.get('rank', '')}",
            f"- Authors: {short_authors(paper.get('authors', []), limit=6)}",
            f"- Published: {paper.get('published', '')}; Category: {paper.get('primary_category', '')}",
            f"- Scores: final={fmt_score(scores.get('final_score'))}, relevance={fmt_score(scores.get('relevance_score_normalized'))}, recency={fmt_score(scores.get('recency_score'))}",
            f"- Recommendation score: {fmt_score(rec_score.get('score'))} (base={fmt_score(rec_score.get('base_score'))})",
            f"- Recommendation: {rec.get('label', 'Context')} / {rec.get('read_priority', 'optional')}. {rec.get('why_recommended', '')}",
            f"- Selection reason: {rec.get('selection_reason', '')}",
            f"- Best for: {', '.join(rec.get('best_for', []) or [])}",
        ])
        if gm:
            lines.append(
                f"- Graph: role={gm.get('network_role', '')}, community={gm.get('community_label', '')}, PageRank={fmt_score(gm.get('pagerank'))}, novelty={fmt_score(gm.get('novelty'))}, bridging={fmt_score(gm.get('bridging_score'))}, network_value={fmt_score(gm.get('network_value_score'))}"
            )
            neighbor_bits = []
            for item in gm.get("nearest_neighbors", [])[:3]:
                shared = ", ".join(item.get("shared_features", [])[:2])
                neighbor_bits.append(f"`{item.get('id')}` ({fmt_score(item.get('weight'))}; {shared})")
            if neighbor_bits:
                lines.append(f"- Closest cached neighbors: {'; '.join(neighbor_bits)}")
        if rec.get("evidence"):
            lines.append(f"- Recommendation evidence: {'; '.join(rec.get('evidence', [])[:5])}")
        if rec.get("caveats"):
            lines.append(f"- Caveats: {'; '.join(rec.get('caveats', []))}")
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

    candidate_limit = min(
        len(ranked_papers),
        max(top_n * 2, len(enriched_by_id), top_n),
    )
    candidate_source = ranked_papers[:candidate_limit]

    candidates = []
    for paper in candidate_source:
        pid = str(paper.get("id"))
        enriched_paper = enriched_by_id.get(pid)
        graph_paper = graph_by_id.get(pid)

        extraction = (
            enriched_paper.get("extraction", extraction_default())
            if isinstance(enriched_paper, dict)
            else extraction_default()
        )

        metrics = (
            graph_paper.get("graph_metrics", {})
            if include_graph_metrics and isinstance(graph_paper, dict)
            else ({} if include_graph_metrics else None)
        )
        candidates.append(paper_card(paper, extraction, metrics))

    reported = select_recommendation_portfolio(candidates, top_n)
    missing_extraction = [
        str(paper.get("id"))
        for paper in reported
        if str(paper.get("id")) not in enriched_by_id
    ]
    missing_metrics = [
        str(paper.get("id"))
        for paper in reported
        if include_graph_metrics and str(paper.get("id")) not in graph_by_id
    ]
    if missing_extraction:
        print(
            "WARN: no extraction found for recommended papers "
            f"{', '.join(missing_extraction)}; using empty defaults",
            file=sys.stderr,
        )
    if missing_metrics:
        print(
            "WARN: no graph metrics found for recommended papers "
            f"{', '.join(missing_metrics)}; using empty metrics",
            file=sys.stderr,
        )

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    visualizations = visualizations_from_graph(run_dir, graph) if graph else []
    report = {
        "query": ranked.get("query"),
        "generated_at": generated_at,
        "report_config": {
            "top_n": top_n,
            "include_graph_metrics": include_graph_metrics,
            "method": "portfolio_network_recommendation_v1",
            "candidate_limit": candidate_limit,
            "portfolio_weights": RECOMMENDATION_WEIGHTS,
            "portfolio_bonuses": PORTFOLIO_BONUSES,
        },
        "source_files": {
            "ranked_papers": "ranked_papers.json",
            "enriched_papers": "enriched_papers.json",
            "graph_metrics": "graph_metrics.json",
            "graph": "graph.json" if graph else None,
            "visualizations": [item["path"] for item in visualizations],
        },
        "counts": {
            "ranked_papers": len(ranked_papers),
            "candidate_papers": len(candidates),
            "enriched_papers": len(enriched.get("papers", []) or []),
            "graph_metric_papers": len(graph_metrics.get("papers", []) or []),
            "reported_papers": len(reported),
        },
        "highlights": pick_highlights(reported, include_graph_metrics),
        "graph_summary": graph.get("graph_summary", {}) if graph else {},
        "research_map": research_map_from_graph(graph) if graph else {"communities": [], "social_summary": {}},
        "visualizations": visualizations,
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
