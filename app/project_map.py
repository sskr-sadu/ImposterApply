from __future__ import annotations

import re
from pathlib import Path

from app.relevance import score_repository
from app.textutil import clip

SKIP_DIRS = {"node_modules", "vendor", "dist", "build", ".git", "venv", ".venv", "coverage"}
SOURCE_EXT = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".php",
    ".cs", ".scala", ".kt", ".sql", ".tf", ".vue", ".svelte",
}
COMMENT_RE = re.compile(r"^\s*(?:#|//|/\*|\*)\s*(.+)$")
DOCSTRING_RE = re.compile(r'^\s*(?:"""|\'\'\')\s*(.+?)(?:"""|\'\'\')?\s*$')


def pick_source_paths(entries: list[dict]) -> list[str]:
    ranked = []
    for item in entries:
        if item.get("type") != "blob":
            continue
        path = str(item.get("path") or "")
        if not path or set(path.split("/")) & SKIP_DIRS:
            continue
        if _is_test(path):
            continue
        size = item.get("size") or 0
        if size and size > 80_000:
            continue
        if Path(path).suffix.lower() not in SOURCE_EXT:
            continue
        ranked.append(path)
    ranked.sort(key=lambda path: (path.count("/"), len(path), path))
    return ranked[:6]


def describe_project(*, repo_description: str, readme: str, source_files: list[dict], languages: list[str]) -> str:
    parts = []
    lead = _sentence(repo_description)
    if lead:
        parts.append(lead)
    readme_lead = _readme_lead(readme)
    if readme_lead and readme_lead not in " ".join(parts):
        parts.append(readme_lead)
    names = [Path(item.get("path") or "").name for item in source_files if item.get("path")]
    if names:
        parts.append("Code files reviewed: " + ", ".join(names[:4]) + ".")
    excerpt = _code_excerpt(source_files)
    if excerpt:
        parts.append(excerpt)
    if languages:
        parts.append("Languages: " + ", ".join(languages[:6]) + ".")
    if not parts:
        return "No README, description, or source files were available to summarize."
    return clip(" ".join(parts), 480)


def suggest_projects(job_description: str, project_map: list[dict], limit: int = 3) -> dict:
    ranked = []
    for project in project_map:
        scored = score_repository(
            job_description,
            {
                "description": project.get("summary") or "",
                "readme": " ".join(
                    [project.get("readme") or "", " ".join(project.get("source_excerpts") or [])]
                ),
                "languages": project.get("languages") or [],
                "files": project.get("files_reviewed") or [],
                "topics": project.get("topics") or [],
            },
        )
        ranked.append({**project, **scored, "why": _why(scored)})
    ranked.sort(key=lambda item: (-item["score"], item.get("full_name") or ""))
    top = [item for item in ranked if item["recommended"]][:limit]
    chosen = {item["full_name"] for item in top}
    others = [item for item in ranked if item["full_name"] not in chosen]
    return {"suggestions": top, "others": others}


def _why(scored: dict) -> str:
    if not scored.get("recommended"):
        return "No terms from the job description appeared in this project's README, code files, or description."
    details = [item.get("detail") or "" for item in scored.get("evidence") or [] if item.get("detail")]
    terms = ", ".join((scored.get("overlap_terms") or [])[:8])
    lead = f"Matches the job description on {terms}." if terms else "Matches the job description."
    if details:
        return lead + " " + " ".join(details[:3])
    return lead


def _readme_lead(readme: str) -> str:
    paragraph = []
    for line in (readme or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("<!--", "![", "[![")):
            if paragraph:
                break
            continue
        if stripped.startswith(("#", "|")) or stripped in {"---", "***"}:
            if paragraph:
                break
            continue
        if stripped.startswith("<") and stripped.endswith(">"):
            continue
        paragraph.append(stripped)
        if len(" ".join(paragraph)) > 220:
            break
    return _sentence(" ".join(paragraph))


def _code_excerpt(source_files: list[dict]) -> str:
    for item in source_files:
        text = item.get("text") or ""
        path = Path(item.get("path") or "file").name
        for line in text.splitlines()[:30]:
            match = DOCSTRING_RE.match(line) or COMMENT_RE.match(line)
            if not match:
                continue
            excerpt = _sentence(match.group(1))
            if len(excerpt) < 12 or excerpt.lower().startswith(("coding", "noqa", "type:", "pylint", "eslint")):
                continue
            return f"{path}: {excerpt}"
    return ""


def _sentence(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text or "").strip(" -*")
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return clip(cleaned, 220)


def _is_test(path: str) -> bool:
    parts = [part.lower() for part in path.split("/")]
    name = parts[-1]
    return "test" in parts[:-1] or name.startswith("test_") or name.endswith("_test.py")
