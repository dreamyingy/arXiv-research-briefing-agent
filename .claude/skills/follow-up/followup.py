#!/usr/bin/env python3
"""follow-up: grounded Q&A over cached arXiv briefing artifacts."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib import request as urlrequest
from urllib.error import URLError


ID_RE = re.compile(r"\b\d{4}\.\d{4,5}\b")
REC_RANK_RE = re.compile(r"(?:rec(?:ommendation)?(?:\s*rank)?|推荐(?:排序|第)?)\s*(\d+)", re.IGNORECASE)
RANK_RE = re.compile(r"(?:rank|#|第)\s*(\d+)", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*")

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "about", "be", "by", "for", "from",
    "how", "i", "in", "is", "it", "me", "more", "of", "on", "or", "paper",
    "papers", "rank", "related", "show", "tell", "the", "this", "to", "vs",
    "what", "which", "with", "compare", "most", "are", "about", "please",
}

LLM_PROVIDERS = {"none", "deepseek", "openai-compatible"}


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
            if key in {"extraction", "graph_metrics", "scores", "recommendation"} and isinstance(value, dict):
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
    graph = load_json(run_dir / "graph.json")

    if not briefing.get("research_map") and graph:
        briefing["research_map"] = {
            "communities": graph.get("communities", []),
            "social_summary": graph.get("social_summary", {}),
        }

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
    papers.sort(key=lambda p: (
        int(p.get("recommendation_rank") or 10**9),
        int(p.get("rank") or 10**9),
    ))
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
    rec_rank = paper.get("recommendation_rank")
    prefix = f"Rec {rec_rank} / Search {paper.get('rank', '?')}" if rec_rank else f"Rank {paper.get('rank', '?')}"
    return f"{prefix} `{paper.get('id', '?')}`: {paper.get('title', '')}"


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


def find_paper_by_recommendation_rank(papers: list[dict], rank: int) -> dict | None:
    for paper in papers:
        if int(paper.get("recommendation_rank") or -1) == rank:
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

    for rank_s in REC_RANK_RE.findall(question):
        paper = find_paper_by_recommendation_rank(papers, int(rank_s))
        if paper and str(paper.get("id")) not in seen:
            found.append(paper)
            seen.add(str(paper.get("id")))

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


def recommendation(paper: dict) -> dict:
    rec = paper.get("recommendation", {})
    if isinstance(rec, dict) and rec:
        return rec
    gm = graph_metrics(paper)
    sc = scores(paper)
    rank = int(paper.get("rank") or 10**9)
    novelty = float(gm.get("novelty", 0.0) or 0.0)
    role = gm.get("network_role", "ranked_candidate")
    if "bridge" in str(role):
        label = "Bridge"
    elif "core" in str(role):
        label = "Core"
    elif novelty >= 0.70:
        label = "Novel"
    elif rank <= 10:
        label = "Relevant"
    else:
        label = "Context"
    priority = "must-read" if rank <= 3 or label in {"Bridge", "Core"} else ("skim" if rank <= 10 else "optional")
    evidence = []
    if rank < 10**9:
        evidence.append(f"rank {rank} in cached ranking")
    if sc.get("final_score") is not None:
        evidence.append(f"final score {fmt_score(sc.get('final_score'))}")
    if gm.get("network_value_score") is not None:
        evidence.append(f"network value {fmt_score(gm.get('network_value_score'))}")
    return {
        "label": label,
        "read_priority": priority,
        "network_role": role,
        "why_recommended": f"Included as a {label.lower()} cached paper based on rank/network signals.",
        "best_for": ["background scanning"] if label == "Context" else ["focused reading"],
        "evidence": evidence,
        "caveats": ["no generated recommendation object was cached for this paper"],
    }


def answer_detail(paper: dict) -> str:
    ext = extraction(paper)
    gm = graph_metrics(paper)
    sc = scores(paper)
    rec = recommendation(paper)
    evidence = ext.get("evidence_sentences", {}) if isinstance(ext.get("evidence_sentences"), dict) else {}
    lines = [
        f"## {paper_label(paper)}",
        "",
        f"- Authors: {short_authors(paper.get('authors', []), limit=8)}",
        f"- Published: {paper.get('published', 'unavailable')}; category: {paper.get('primary_category', 'unavailable')}",
        f"- URL: {paper.get('url', 'unavailable')}",
        f"- Scores: final={fmt_score(sc.get('final_score'))}, relevance={fmt_score(sc.get('relevance_score_normalized'))}, recency={fmt_score(sc.get('recency_score'))}",
        f"- Graph: role={gm.get('network_role', 'unavailable')}, community={gm.get('community_label', 'unavailable')}, PageRank={fmt_score(gm.get('pagerank'))}, novelty={fmt_score(gm.get('novelty'))}, bridging={fmt_score(gm.get('bridging_score'))}, network_value={fmt_score(gm.get('network_value_score'))}",
        f"- Recommendation: {rec.get('label', 'Context')} / {rec.get('read_priority', 'optional')}. {rec.get('why_recommended', 'No generated recommendation reason is cached yet.')}",
        f"- Best for: {', '.join(rec.get('best_for', []) or []) or 'unavailable'}",
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
        f"- Recommendation evidence: {'; '.join(rec.get('evidence', []) or []) or 'unavailable'}",
        f"- Caveats: {'; '.join(rec.get('caveats', []) or []) or 'none cached'}",
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
        ("Network role", graph_metrics(left).get("network_role", ""), graph_metrics(right).get("network_role", "")),
        ("Recommendation", recommendation(left).get("why_recommended", ""), recommendation(right).get("why_recommended", "")),
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
        rec = recommendation(paper)
        lines.append(
            f"- {paper_label(paper)} | final={fmt_score(scores(paper).get('final_score'))} | "
            f"{rec.get('label', 'Context')} | keywords={', '.join(ext.get('keywords', [])[:5] if ext.get('keywords') else [])} | {paper.get('url', '')}"
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
        role = gm.get("network_role")
        if role:
            metric_parts.append(f"role={role}")
        lines.append(f"- {paper_label(paper)} | {' | '.join(metric_parts)} | {paper.get('url', '')}")
    return "\n".join(lines)


def answer_top(papers: list[dict], top_k: int) -> str:
    lines = ["## Top Ranked Cached Papers", ""]
    for paper in papers[:top_k]:
        rec = recommendation(paper)
        lines.append(
            f"- {paper_label(paper)} | final={fmt_score(scores(paper).get('final_score'))} | "
            f"{rec.get('label', 'Context')} / {rec.get('read_priority', 'optional')} | {paper.get('url', '')}"
        )
    return "\n".join(lines)


def answer_recommendations(papers: list[dict], top_k: int) -> str:
    def priority_rank(paper: dict) -> tuple[int, int, int]:
        priority = recommendation(paper).get("read_priority", "optional")
        order = {"must-read": 0, "skim": 1, "optional": 2}
        return (
            int(paper.get("recommendation_rank") or 10**9),
            order.get(priority, 3),
            int(paper.get("rank") or 10**9),
        )

    ranked = sorted(papers, key=priority_rank)
    lines = ["## Recommended Reading Picks", ""]
    for paper in ranked[:top_k]:
        rec = recommendation(paper)
        lines.append(f"- {paper_label(paper)}")
        lines.append(f"  Reason: {rec.get('why_recommended', 'included by upstream ranking')}")
        lines.append(f"  Best for: {', '.join(rec.get('best_for', []) or []) or 'background scanning'}")
        lines.append(f"  Evidence: {'; '.join(rec.get('evidence', [])[:4]) or 'unavailable'}")
    return "\n".join(lines)


def answer_reading_plan(papers: list[dict], top_k: int) -> str:
    core = [
        p for p in papers
        if recommendation(p).get("read_priority") == "must-read"
        or graph_metrics(p).get("network_role") in {"community_core", "bridge_hub"}
    ][:3]
    novel = sorted(papers, key=lambda p: float(graph_metrics(p).get("novelty", 0.0) or 0.0), reverse=True)[:2]
    bridge = sorted(papers, key=lambda p: float(graph_metrics(p).get("bridging_score", 0.0) or 0.0), reverse=True)[:2]

    ordered: list[tuple[str, dict]] = []
    seen: set[str] = set()
    for label, group in [("Start here", core), ("Then map adjacent threads", bridge), ("Finish with novelty scan", novel)]:
        for paper in group:
            pid = str(paper.get("id"))
            if pid not in seen:
                ordered.append((label, paper))
                seen.add(pid)
    if not ordered:
        ordered = [("Start here", p) for p in papers[:top_k]]

    lines = ["## Reading Plan", ""]
    for idx, (step, paper) in enumerate(ordered[:top_k], start=1):
        rec = recommendation(paper)
        lines.append(
            f"{idx}. **{step}**: {paper_label(paper)} | "
            f"{rec.get('label', 'Context')} | {rec.get('why_recommended', '')} | {paper.get('url', '')}"
        )
    return "\n".join(lines)


def answer_communities(briefing: dict, papers: list[dict], top_k: int) -> str:
    research_map = briefing.get("research_map", {}) if isinstance(briefing.get("research_map"), dict) else {}
    communities = research_map.get("communities", []) if isinstance(research_map, dict) else []
    if not communities:
        return "No cached community map is available. Run paper-network and paper-report again to refresh graph insights."

    by_id = {str(p.get("id")): p for p in papers}
    lines = ["## Cached Research Communities", ""]
    for community in communities[:top_k]:
        lines.append(
            f"- Community {community.get('community_id')}: **{community.get('label', '')}** "
            f"({community.get('size', 0)} papers)"
        )
        reps = []
        for pid in community.get("representative_papers", [])[:3]:
            paper = by_id.get(str(pid))
            reps.append(paper_label(paper) if paper else f"`{pid}`")
        if reps:
            lines.append(f"  Representative papers: {'; '.join(reps)}")
        topics = ", ".join(item.get("label", "") for item in community.get("top_topics", [])[:5])
        if topics:
            lines.append(f"  Topics: {topics}")
    return "\n".join(lines)


def answer_similar(question: str, papers: list[dict], top_k: int) -> str:
    identified = identify_papers(question, papers)
    if not identified:
        return "Tell me which cached paper to use as the anchor, for example `similar to rank 1`.\n\n" + available_identifiers(papers, top_k)
    anchor = identified[0]
    by_id = {str(p.get("id")): p for p in papers}
    neighbors = graph_metrics(anchor).get("nearest_neighbors", [])
    if not neighbors:
        return f"No cached nearest-neighbor links are available for {paper_label(anchor)}."
    lines = [f"## Papers Similar To {paper_label(anchor)}", ""]
    for item in neighbors[:top_k]:
        paper = by_id.get(str(item.get("id")))
        label = paper_label(paper) if paper else f"`{item.get('id')}`"
        shared = ", ".join(item.get("shared_features", [])[:5]) or "shared network features"
        lines.append(
            f"- {label} | edge_weight={fmt_score(item.get('weight'))} | shared={shared}"
        )
    return "\n".join(lines)


def answer_practical(papers: list[dict], top_k: int) -> str:
    scored = []
    for paper in papers:
        ext = extraction(paper)
        score = 0
        score += len(ext.get("evaluation_signals", []) or []) * 2
        score += len(ext.get("datasets_or_domains", []) or [])
        if paper.get("comment"):
            score += 1
        if score:
            scored.append((score, paper))
    scored.sort(key=lambda item: (-item[0], int(item[1].get("rank") or 10**9)))
    if not scored:
        return "No cached papers expose strong evaluation or dataset signals in the current extraction."
    lines = ["## Most Practical / Evidence-Rich Cached Papers", ""]
    for score, paper in scored[:top_k]:
        ext = extraction(paper)
        lines.append(
            f"- {paper_label(paper)} | practical_signal={score} | "
            f"datasets={', '.join(ext.get('datasets_or_domains', [])[:4] if ext.get('datasets_or_domains') else []) or 'unavailable'} | "
            f"evaluation={'; '.join(ext.get('evaluation_signals', [])[:2] if ext.get('evaluation_signals') else []) or 'unavailable'}"
        )
    return "\n".join(lines)


def llm_context(briefing: dict, papers: list[dict], top_k: int) -> str:
    rows = []
    for paper in papers[:max(top_k, 8)]:
        rows.append({
            "id": paper.get("id"),
            "rank": paper.get("rank"),
            "title": paper.get("title"),
            "url": paper.get("url"),
            "scores": scores(paper),
            "extraction": extraction(paper),
            "graph_metrics": graph_metrics(paper),
            "recommendation": recommendation(paper),
        })
    payload = {
        "query": briefing.get("query"),
        "highlights": briefing.get("highlights"),
        "research_map": briefing.get("research_map"),
        "papers": rows,
    }
    return json.dumps(payload, ensure_ascii=False)


def answer_with_llm(question: str, briefing: dict, papers: list[dict],
                    top_k: int, provider: str, base_url: str | None,
                    model: str | None) -> str:
    if provider == "none":
        raise RuntimeError("LLM provider is disabled")
    if provider == "deepseek":
        api_key = os.environ.get("DEEPSEEK_API_KEY")
        base_url = base_url or "https://api.deepseek.com/chat/completions"
        model = model or "deepseek-chat"
    else:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("LLM_API_KEY")
        if not base_url:
            raise RuntimeError("--llm-base-url is required for openai-compatible provider")
        model = model or "gpt-4o-mini"
    if not api_key:
        raise RuntimeError(f"missing API key for {provider}")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a grounded research briefing assistant. Answer only from the JSON context. "
                "Cite arXiv IDs/ranks/URLs. If a fact is missing, say it is unavailable in cached artifacts."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {question}\n\nCached JSON context:\n{llm_context(briefing, papers, top_k)}",
        },
    ]
    body = json.dumps({"model": model, "messages": messages, "temperature": 0.2}, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        base_url,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=45) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except URLError as exc:
        raise RuntimeError(f"LLM request failed: {exc}") from exc
    return payload["choices"][0]["message"]["content"].strip()


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

    if any(term in q for term in ["similar", "nearest", "neighbor", "相似", "相关论文", "类似"]):
        return answer_similar(question, papers, top_k)

    if identified:
        if any(term in q for term in ["why", "recommend", "推荐", "为什么", "理由"]):
            rec = recommendation(identified[0])
            return (
                f"## Why This Paper Was Recommended\n\n"
                f"{paper_label(identified[0])}\n\n"
                f"- Reason: {rec.get('why_recommended', 'No generated recommendation reason is cached yet.')}\n"
                f"- Best for: {', '.join(rec.get('best_for', []) or []) or 'unavailable'}\n"
                f"- Evidence: {'; '.join(rec.get('evidence', []) or []) or 'unavailable'}\n"
                f"- Caveats: {'; '.join(rec.get('caveats', []) or []) or 'none cached'}"
            )
        return answer_detail(identified[0])

    if any(term in q for term in ["reading", "read order", "plan", "route", "路线", "阅读顺序", "怎么读"]):
        return answer_reading_plan(papers, top_k)

    if any(term in q for term in ["recommend", "why read", "推荐", "值得读", "必读"]):
        return answer_recommendations(papers, top_k)

    if any(term in q for term in ["community", "cluster", "topic group", "社区", "分组", "研究方向"]):
        return answer_communities(briefing, papers, top_k)

    if any(term in q for term in ["practical", "reproduce", "reproducible", "dataset", "evaluation", "实验", "复现", "数据集"]):
        return answer_practical(papers, top_k)

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
    parser.add_argument("--llm-provider", choices=sorted(LLM_PROVIDERS), default="none",
                        help="Optional grounded LLM synthesis provider (default: none)")
    parser.add_argument("--llm-base-url", default=None,
                        help="OpenAI-compatible chat completions URL")
    parser.add_argument("--llm-model", default=None,
                        help="Model name for --llm-provider")
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
        if args.llm_provider != "none":
            try:
                answer = answer_with_llm(
                    question,
                    briefing,
                    papers,
                    args.top_k,
                    args.llm_provider,
                    args.llm_base_url,
                    args.llm_model,
                )
            except RuntimeError as exc:
                answer = (
                    f"LLM synthesis unavailable ({exc}); falling back to cached template mode.\n\n"
                    + answer_question(question, briefing, papers, args.top_k)
                )
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
