#!/usr/bin/env python3
"""paper-network: graph construction and metrics for the arXiv briefing agent.

Reads <input-dir>/ranked_papers.json plus optional enriched_papers.json, writes
graph.json and graph_metrics.json next to them. Does not create run dirs and
does not touch latest_run.txt.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

try:
    import networkx as nx
except ImportError:
    sys.exit("ERROR: 'networkx' not installed. Run: pip install networkx")


TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
SLUG_RE = re.compile(r"[^a-z0-9]+")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "had", "in", "into", "is", "it", "its", "of", "on", "or",
    "that", "the", "this", "to", "was", "we", "were", "with", "which", "while",
    "when", "where", "who", "what", "how", "why", "our", "their", "they",
    "them", "these", "those", "using", "used", "based", "also", "can", "may",
    "more", "than", "not", "no", "both", "other", "one", "two", "each", "any",
    "all", "very", "most", "some", "new", "via", "through", "over", "between",
    "among", "without", "within", "across", "about", "upon", "should", "would",
    "could", "will", "shall", "might", "must", "do", "does", "did", "been",
    "being", "there", "here", "however", "thus", "hence", "if", "then", "so",
    "yet", "still", "only", "just", "even", "paper", "study", "studies",
    "result", "results", "show", "shows", "propose", "proposes", "present",
    "presents", "method", "model", "models", "approach", "learning",
}

AUTHOR_WEIGHT = 3.0
CATEGORY_WEIGHT = 1.5
TOPIC_WEIGHT = 1.0


def resolve_input_dir(input_dir_flag: Path | None) -> Path:
    if input_dir_flag is not None:
        if not input_dir_flag.is_dir():
            sys.exit(f"ERROR: --input-dir not found or not a directory: {input_dir_flag}")
        return input_dir_flag

    latest = Path("output") / "latest_run.txt"
    if not latest.exists():
        sys.exit(
            "ERROR: ./output/latest_run.txt not found. "
            "Run paper-search and paper-rank first, or pass --input-dir."
        )
    run_id = latest.read_text(encoding="utf-8").strip()
    run_dir = Path("output") / run_id
    if not run_dir.is_dir():
        sys.exit(f"ERROR: run directory referenced by latest_run.txt is missing: {run_dir}")
    return run_dir


def load_ranked(run_dir: Path) -> dict:
    path = run_dir / "ranked_papers.json"
    if not path.exists():
        sys.exit(f"ERROR: ranked_papers.json not found in {run_dir}. Run paper-rank first.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: ranked_papers.json is not valid JSON: {e}")
    if "papers" not in data:
        sys.exit("ERROR: ranked_papers.json missing required key 'papers'")
    return data


def load_enriched_by_id(run_dir: Path) -> dict[str, dict]:
    path = run_dir / "enriched_papers.json"
    if not path.exists():
        print(
            f"WARN: enriched_papers.json not found in {run_dir}; "
            "falling back to title/abstract/category topics",
            file=sys.stderr,
        )
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(
            f"WARN: enriched_papers.json is not valid JSON ({e}); "
            "falling back to title/abstract/category topics",
            file=sys.stderr,
        )
        return {}
    papers = data.get("papers", [])
    if not isinstance(papers, list):
        print("WARN: enriched_papers.json missing list key 'papers'; ignoring it", file=sys.stderr)
        return {}
    return {p.get("id"): p for p in papers if p.get("id")}


def normalize_space(s: str) -> str:
    return " ".join((s or "").split())


def slugify(s: str) -> str:
    slug = SLUG_RE.sub("-", s.lower()).strip("-")
    return slug or "unknown"


def fallback_topics(paper: dict, limit: int) -> list[str]:
    title = paper.get("title", "") or ""
    abstract = paper.get("abstract", "") or ""
    text = f"{title} {abstract}".lower()
    tokens = [
        t for t in TOKEN_RE.findall(text)
        if t not in STOPWORDS and len(t) > 1 and not t.isdigit()
    ]
    counts = Counter(tokens)

    title_tokens = [
        t for t in TOKEN_RE.findall(title.lower())
        if t not in STOPWORDS and len(t) > 1 and not t.isdigit()
    ]
    for tok in title_tokens:
        counts[tok] += 2

    for cat in paper.get("categories", []) or []:
        clean = str(cat).lower()
        if clean:
            counts[clean] += 3

    scored = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [tok for tok, _ in scored[:limit]]


def topics_for_paper(paper: dict, enriched_by_id: dict[str, dict], limit: int,
                     include_topics: bool) -> list[str]:
    if not include_topics:
        return []

    enriched = enriched_by_id.get(paper.get("id"), {})
    extraction = enriched.get("extraction", {}) if isinstance(enriched, dict) else {}
    candidates: list[str] = []
    for key in ("keywords", "datasets_or_domains"):
        vals = extraction.get(key, [])
        if isinstance(vals, list):
            candidates.extend(str(v) for v in vals if str(v).strip())

    if not candidates:
        candidates = fallback_topics(paper, limit)

    seen: set[str] = set()
    out: list[str] = []
    for item in candidates:
        topic = normalize_space(item).lower()
        if not topic or topic in seen:
            continue
        seen.add(topic)
        out.append(topic)
        if len(out) >= limit:
            break
    return out


def paper_node_id(paper_id: str) -> str:
    return f"paper:{paper_id}"


def author_node_id(author: str) -> str:
    return f"author:{slugify(author)}"


def category_node_id(category: str) -> str:
    return f"category:{category}"


def topic_node_id(topic: str) -> str:
    return f"topic:{slugify(topic)}"


def add_or_increment_edge(g: nx.Graph, source: str, target: str, edge_type: str,
                          weight: float, feature: str | None = None) -> None:
    if g.has_edge(source, target):
        g[source][target]["weight"] += weight
        types = set(g[source][target].get("types", []))
        types.add(edge_type)
        g[source][target]["types"] = sorted(types)
        if feature:
            features = set(g[source][target].get("shared_features", []))
            features.add(feature)
            g[source][target]["shared_features"] = sorted(features)
        return

    attrs = {"weight": weight, "types": [edge_type]}
    if feature:
        attrs["shared_features"] = [feature]
    g.add_edge(source, target, **attrs)


def build_graphs(papers: list[dict], enriched_by_id: dict[str, dict],
                 topic_limit: int, include_topics: bool,
                 min_paper_edge_weight: float,
                 max_feature_paper_fraction: float) -> tuple[nx.Graph, nx.Graph, dict[str, dict]]:
    hetero = nx.Graph()
    projection = nx.Graph()
    features_by_paper: dict[str, dict] = {}
    feature_to_papers: dict[str, list[str]] = defaultdict(list)

    for paper in papers:
        pid = str(paper.get("id", "")).strip()
        if not pid:
            continue

        pnode = paper_node_id(pid)
        authors = [normalize_space(str(a)) for a in paper.get("authors", []) or [] if str(a).strip()]
        categories = [normalize_space(str(c)) for c in paper.get("categories", []) or [] if str(c).strip()]
        topics = topics_for_paper(paper, enriched_by_id, topic_limit, include_topics)

        hetero.add_node(
            pnode,
            type="paper",
            label=normalize_space(paper.get("title", "")),
            id=pid,
            rank=paper.get("rank"),
            published=paper.get("published"),
            url=paper.get("url"),
        )
        projection.add_node(
            pid,
            type="paper",
            label=normalize_space(paper.get("title", "")),
            id=pid,
            rank=paper.get("rank"),
        )

        for author in authors:
            anode = author_node_id(author)
            hetero.add_node(anode, type="author", label=author)
            hetero.add_edge(pnode, anode, type="paper-author", weight=1.0)
            feature_to_papers[f"author:{author.lower()}"].append(pid)

        for category in categories:
            cnode = category_node_id(category)
            hetero.add_node(cnode, type="category", label=category)
            hetero.add_edge(pnode, cnode, type="paper-category", weight=1.0)
            feature_to_papers[f"category:{category}"].append(pid)

        for topic in topics:
            tnode = topic_node_id(topic)
            hetero.add_node(tnode, type="topic", label=topic)
            hetero.add_edge(pnode, tnode, type="paper-topic", weight=1.0)
            feature_to_papers[f"topic:{topic}"].append(pid)

        for a, b in combinations(authors, 2):
            hetero.add_edge(author_node_id(a), author_node_id(b), type="coauthor", weight=1.0)

        features_by_paper[pid] = {
            "authors": authors,
            "categories": categories,
            "topics": topics,
        }

    total_papers = max(len(features_by_paper), 1)
    for feature, paper_ids in feature_to_papers.items():
        unique_ids = sorted(set(paper_ids))
        if len(unique_ids) < 2:
            continue
        if len(unique_ids) / total_papers > max_feature_paper_fraction:
            continue
        if feature.startswith("author:"):
            weight = AUTHOR_WEIGHT
            edge_type = "shared-author"
        elif feature.startswith("category:"):
            weight = CATEGORY_WEIGHT
            edge_type = "shared-category"
        else:
            weight = TOPIC_WEIGHT
            edge_type = "shared-topic"

        for left, right in combinations(unique_ids, 2):
            add_or_increment_edge(projection, left, right, edge_type, weight, feature)

    weak_edges = [
        (u, v) for u, v, attrs in projection.edges(data=True)
        if float(attrs.get("weight", 0.0)) < min_paper_edge_weight
    ]
    projection.remove_edges_from(weak_edges)

    return hetero, projection, features_by_paper


def normalize(values: dict[str, float], default: float = 0.0) -> dict[str, float]:
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi == lo:
        return {k: default for k in values}
    return {k: (v - lo) / (hi - lo) for k, v in values.items()}


def compute_metrics(projection: nx.Graph, features_by_paper: dict[str, dict]) -> dict[str, dict]:
    paper_ids = list(projection.nodes())
    if not paper_ids:
        return {}

    if projection.number_of_edges() == 0:
        degree = {pid: 0.0 for pid in paper_ids}
        between = {pid: 0.0 for pid in paper_ids}
        pagerank = {pid: 1.0 / len(paper_ids) for pid in paper_ids}
    else:
        for _, _, attrs in projection.edges(data=True):
            weight = float(attrs.get("weight", 1.0))
            attrs["distance"] = 1.0 / weight if weight > 0 else 1.0
        degree = nx.degree_centrality(projection)
        between = nx.betweenness_centrality(projection, weight="distance", normalized=True)
        pagerank = nx.pagerank(projection, weight="weight")

    weighted_degree = {
        pid: sum(float(attrs.get("weight", 1.0)) for _, _, attrs in projection.edges(pid, data=True))
        for pid in paper_ids
    }
    weighted_degree_norm = normalize(weighted_degree, default=0.5)

    metrics: dict[str, dict] = {}
    for pid in paper_ids:
        features = features_by_paper.get(pid, {})
        novelty = 1.0 - weighted_degree_norm.get(pid, 0.0)
        metrics[pid] = {
            "degree_centrality": round(float(degree.get(pid, 0.0)), 6),
            "betweenness_centrality": round(float(between.get(pid, 0.0)), 6),
            "pagerank": round(float(pagerank.get(pid, 0.0)), 6),
            "weighted_degree": round(float(weighted_degree.get(pid, 0.0)), 6),
            "novelty": round(float(novelty), 6),
            "bridging_score": round(float(between.get(pid, 0.0)), 6),
            "author_count": len(features.get("authors", [])),
            "topic_count": len(features.get("topics", [])),
            "category_count": len(features.get("categories", [])),
        }
    return metrics


def graph_nodes_json(g: nx.Graph) -> list[dict]:
    nodes = []
    for node_id, attrs in g.nodes(data=True):
        nodes.append({"node_id": node_id, **attrs})
    nodes.sort(key=lambda n: (n.get("type", ""), n.get("label", ""), n["node_id"]))
    return nodes


def graph_edges_json(g: nx.Graph) -> list[dict]:
    edges = []
    for source, target, attrs in g.edges(data=True):
        edge = {
            "source": source,
            "target": target,
            "type": attrs.get("type") or "+".join(attrs.get("types", [])),
            "weight": round(float(attrs.get("weight", 1.0)), 6),
        }
        if "types" in attrs:
            edge["types"] = attrs["types"]
        if "shared_features" in attrs:
            edge["shared_features"] = attrs["shared_features"]
        edges.append(edge)
    edges.sort(key=lambda e: (e["source"], e["target"], e["type"]))
    return edges


def projection_edges_json(g: nx.Graph) -> list[dict]:
    edges = []
    for source, target, attrs in g.edges(data=True):
        edges.append({
            "source": source,
            "target": target,
            "weight": round(float(attrs.get("weight", 1.0)), 6),
            "types": attrs.get("types", []),
            "shared_features": attrs.get("shared_features", []),
        })
    edges.sort(key=lambda e: (-e["weight"], e["source"], e["target"]))
    return edges


def write_empty_outputs(run_dir: Path, ranked: dict, config: dict) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    graph_payload = {
        "query": ranked.get("query"),
        "fetched_at": ranked.get("fetched_at"),
        "ranked_at": ranked.get("ranked_at"),
        "networked_at": now,
        "network_config": config,
        "count": 0,
        "graph_summary": {
            "node_count": 0,
            "edge_count": 0,
            "paper_projection_node_count": 0,
            "paper_projection_edge_count": 0,
        },
        "nodes": [],
        "edges": [],
        "paper_projection_edges": [],
    }
    metrics_payload = {
        "query": ranked.get("query"),
        "fetched_at": ranked.get("fetched_at"),
        "ranked_at": ranked.get("ranked_at"),
        "ranking_config": ranked.get("ranking_config"),
        "networked_at": now,
        "network_config": config,
        "count": 0,
        "papers": [],
    }
    (run_dir / "graph.json").write_text(
        json.dumps(graph_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (run_dir / "graph_metrics.json").write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build paper/author/category/topic graph metrics for ranked papers")
    parser.add_argument("--input-dir", type=Path, default=None,
                        help="Run directory containing ranked_papers.json "
                             "(default: resolved from ./output/latest_run.txt)")
    parser.add_argument("--topic-limit", type=int, default=8,
                        help="Max topic keywords/domains per paper (default 8)")
    parser.add_argument("--min-paper-edge-weight", type=float, default=1.0,
                        help="Minimum paper projection edge weight to keep (default 1.0)")
    parser.add_argument("--max-feature-paper-fraction", type=float, default=0.70,
                        help="Ignore projection features shared by more than this fraction "
                             "of papers (default 0.70)")
    parser.add_argument("--no-topics", action="store_true",
                        help="Skip topic nodes and topic-based paper similarity")
    args = parser.parse_args()

    if args.topic_limit <= 0:
        sys.exit(f"ERROR: --topic-limit must be positive, got {args.topic_limit}")
    if args.min_paper_edge_weight < 0:
        sys.exit(
            "ERROR: --min-paper-edge-weight must be non-negative, "
            f"got {args.min_paper_edge_weight}"
        )
    if not 0 < args.max_feature_paper_fraction <= 1:
        sys.exit(
            "ERROR: --max-feature-paper-fraction must be in (0, 1], "
            f"got {args.max_feature_paper_fraction}"
        )

    run_dir = resolve_input_dir(args.input_dir)
    ranked = load_ranked(run_dir)
    papers = ranked.get("papers", [])

    config = {
        "method": "heterogeneous_graph_plus_paper_projection_v1",
        "topic_limit": args.topic_limit,
        "include_topics": not args.no_topics,
        "min_paper_edge_weight": args.min_paper_edge_weight,
        "max_feature_paper_fraction": args.max_feature_paper_fraction,
        "projection_weights": {
            "shared_author": AUTHOR_WEIGHT,
            "shared_category": CATEGORY_WEIGHT,
            "shared_topic": TOPIC_WEIGHT,
        },
    }

    if not papers:
        write_empty_outputs(run_dir, ranked, config)
        print(f"WARN: empty corpus -> {run_dir / 'graph.json'}", file=sys.stderr)
        return 0

    enriched_by_id = load_enriched_by_id(run_dir)
    hetero, projection, features_by_paper = build_graphs(
        papers,
        enriched_by_id,
        args.topic_limit,
        not args.no_topics,
        args.min_paper_edge_weight,
        args.max_feature_paper_fraction,
    )
    metrics = compute_metrics(projection, features_by_paper)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")

    graph_payload = {
        "query": ranked.get("query"),
        "fetched_at": ranked.get("fetched_at"),
        "ranked_at": ranked.get("ranked_at"),
        "networked_at": now,
        "network_config": config,
        "count": len(papers),
        "graph_summary": {
            "node_count": hetero.number_of_nodes(),
            "edge_count": hetero.number_of_edges(),
            "paper_projection_node_count": projection.number_of_nodes(),
            "paper_projection_edge_count": projection.number_of_edges(),
        },
        "nodes": graph_nodes_json(hetero),
        "edges": graph_edges_json(hetero),
        "paper_projection_edges": projection_edges_json(projection),
    }

    metrics_payload = {
        "query": ranked.get("query"),
        "fetched_at": ranked.get("fetched_at"),
        "ranked_at": ranked.get("ranked_at"),
        "ranking_config": ranked.get("ranking_config"),
        "networked_at": now,
        "network_config": config,
        "count": len(papers),
        "papers": [
            {**paper, "graph_metrics": metrics.get(str(paper.get("id")), {})}
            for paper in papers
        ],
    }

    graph_path = run_dir / "graph.json"
    metrics_path = run_dir / "graph_metrics.json"
    graph_path.write_text(
        json.dumps(graph_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    metrics_path.write_text(
        json.dumps(metrics_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"INFO: wrote graph with {hetero.number_of_nodes()} nodes, "
        f"{hetero.number_of_edges()} edges -> {graph_path}",
        file=sys.stderr,
    )
    print(f"INFO: wrote graph metrics for {len(papers)} papers -> {metrics_path}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
