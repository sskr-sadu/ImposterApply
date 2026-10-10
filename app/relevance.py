from __future__ import annotations

import re

from app.textutil import clip, tokens, unique

WEIGHTS = {"languages": 3, "topics": 3, "files": 2, "description": 2, "readme": 1}


def select_repos(selected: list[str], eligible: list[dict]) -> list[dict]:
    by_name = {repo["full_name"]: repo for repo in eligible}
    chosen = []
    rejected = []
    for name in selected:
        repo = by_name.get(name)
        if repo is None:
            rejected.append(name)
            continue
        chosen.append(repo)
    if rejected:
        raise SelectionError(rejected)
    return chosen


class SelectionError(Exception):
    def __init__(self, names: list[str]):
        self.names = names
        super().__init__(", ".join(names))


def filter_repos(repos: list[dict], visibility: str) -> list[dict]:
    if visibility not in {"public", "private", "both"}:
        raise ValueError(visibility)
    kept = []
    for repo in repos:
        private = bool(repo.get("private"))
        if visibility == "public" and private:
            continue
        if visibility == "private" and not private:
            continue
        kept.append(repo)
    return kept


def score_repository(job_description: str, evidence: dict) -> dict:
    job_terms = unique(tokens(job_description))
    fields = {
        "description": tokens(evidence.get("description") or ""),
        "readme": tokens(evidence.get("readme") or ""),
        "languages": tokens(" ".join(evidence.get("languages") or [])),
        "files": tokens(" ".join(evidence.get("files") or [])),
        "topics": tokens(" ".join(evidence.get("topics") or [])),
    }
    matched: dict[str, list[str]] = {}
    for field, field_tokens in fields.items():
        overlap = [term for term in job_terms if term in set(field_tokens)]
        if overlap:
            matched[field] = overlap
    overlap_terms = unique([term for values in matched.values() for term in values])
    score = sum(WEIGHTS[field] * len(values) for field, values in matched.items())
    evidence_items = []
    for field, values in matched.items():
        evidence_items.append(
            {
                "source": field,
                "detail": _detail(field, values, evidence),
            }
        )
    return {
        "score": score,
        "overlap_terms": overlap_terms,
        "evidence": evidence_items,
        "recommended": score > 0,
    }


def _detail(field: str, values: list[str], evidence: dict) -> str:
    joined = ", ".join(values[:8])
    if field == "readme":
        snippet = _snippet(evidence.get("readme") or "", values)
        if snippet:
            return f"README mentions {joined}: “{snippet}”"
        return f"README mentions {joined}"
    if field == "files":
        names = ", ".join((evidence.get("files") or [])[:6])
        return f"Files ({names}) overlap on {joined}"
    if field == "languages":
        return f"Repository languages include {joined}"
    if field == "topics":
        return f"Repository topics include {joined}"
    return f"Repository description overlaps on {joined}"


def _snippet(readme: str, terms: list[str]) -> str:
    if not readme or not terms:
        return ""
    pattern = re.compile("|".join(re.escape(term) for term in terms[:6]), re.IGNORECASE)
    match = pattern.search(readme)
    if not match:
        return clip(readme, 160)
    start = max(0, match.start() - 60)
    end = min(len(readme), match.end() + 80)
    return clip(re.sub(r"\s+", " ", readme[start:end]), 180)
