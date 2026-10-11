from __future__ import annotations

import re

from app.textutil import digit_chunks, tokens, unique


def build_draft(
    *,
    job_description: str,
    contact: dict,
    experience: list[dict],
    education: list[dict],
    skills_text: str,
    additional_text: str,
    projects: list[dict],
) -> dict:
    reviewed_sources = _source_blob(experience, education, skills_text, additional_text, projects)
    shared = [
        term
        for term in unique(tokens(job_description))
        if term in set(tokens(reviewed_sources)) and not re.search(r"\d", term)
    ]
    summary = _summary(experience, shared)
    drafted_experience = [_emphasize(entry, shared) for entry in experience]
    drafted_projects = [_project_block(project) for project in projects if project.get("selected")]
    links = contact.get("links") or []
    contact_bits = [contact.get("email") or "", contact.get("phone") or "", contact.get("location") or ""]
    contact_bits.extend(links)
    return {
        "name": contact.get("name") or "",
        "contact_line": "  ·  ".join(bit for bit in contact_bits if bit),
        "summary": summary,
        "experience": drafted_experience,
        "education": [
            {
                "school": item.get("school") or "",
                "credential": item.get("credential") or "",
                "dates": _dates(item.get("start") or "", item.get("end") or ""),
                "details": item.get("details") or "",
            }
            for item in education
        ],
        "skills": skills_text or "",
        "additional": additional_text or "",
        "projects": drafted_projects,
        "shared_terms": shared,
    }


def generated_text(draft: dict) -> str:
    parts = [draft.get("summary") or ""]
    for project in draft.get("projects") or []:
        parts.append(project.get("description") or "")
    return "\n".join(parts)


def source_text_for_digits(*, experience, education, skills_text, additional_text, projects) -> str:
    return _source_blob(experience, education, skills_text, additional_text, projects)


def assert_grounded(draft: dict, allowed_digits: set[str]) -> None:
    introduced = digit_chunks(generated_text(draft)) - allowed_digits
    if introduced:
        raise ValueError(f"generated text introduced numbers: {sorted(introduced)}")


def _summary(experience: list[dict], shared: list[str]) -> str:
    roles = []
    for entry in experience:
        title = (entry.get("title") or "").strip()
        employer = (entry.get("employer") or "").strip()
        if title and employer:
            roles.append(f"{title} at {employer}")
        elif title or employer:
            roles.append(title or employer)
    sentences = []
    if roles:
        sentences.append("Reviewed experience: " + "; ".join(roles) + ".")
    if shared:
        sentences.append(
            "Terms that appear in both the job description and reviewed sources: "
            + ", ".join(shared[:12])
            + "."
        )
    else:
        sentences.append(
            "No shared terms were found between the job description and reviewed sources. "
            "Wording below preserves those sources without added claims."
        )
    return " ".join(sentences)


def _emphasize(entry: dict, shared: list[str]) -> dict:
    description = entry.get("description") or ""
    lines = description.splitlines()
    if shared and len(lines) > 1:
        description = "\n".join(_partition(lines, shared))
    return {
        "employer": entry.get("employer") or "",
        "title": entry.get("title") or "",
        "dates": _dates(entry.get("start") or "", entry.get("end") or ""),
        "description": description,
    }


def _partition(lines: list[str], shared: list[str]) -> list[str]:
    matched = []
    rest = []
    for line in lines:
        if line.strip() and any(term in tokens(line) for term in shared):
            matched.append(line)
        else:
            rest.append(line)
    return matched + rest


def _project_block(project: dict) -> dict:
    private = bool(project.get("private"))
    description = (project.get("summary") or project.get("description") or "").strip()
    return {
        "full_name": project.get("full_name") or "",
        "private": private,
        "include_in_export": not private,
        "include_link": not private,
        "link": project.get("html_url") or "",
        "heading": project.get("full_name") or "Repository",
        "description": description,
    }


def _dates(start: str, end: str) -> str:
    if start and end:
        return f"{start} – {end}"
    return start or end or ""


def _source_blob(experience, education, skills_text, additional_text, projects) -> str:
    parts = [skills_text or "", additional_text or ""]
    for entry in experience:
        parts.extend([entry.get("title") or "", entry.get("employer") or "", entry.get("description") or ""])
    for entry in education:
        parts.extend(
            [entry.get("school") or "", entry.get("credential") or "", entry.get("details") or ""]
        )
    for project in projects:
        if project.get("selected"):
            parts.append(project.get("summary") or project.get("description") or "")
    return "\n".join(parts)


def project_export_text(project: dict) -> str:
    if project.get("private") and not project.get("include_in_export"):
        return ""
    if not project.get("include_in_export", True):
        return ""
    parts = [project.get("heading") or "", project.get("description") or ""]
    if project.get("include_link") and project.get("link"):
        parts.append(project.get("link") or "")
    return "\n".join(part for part in parts if part)
