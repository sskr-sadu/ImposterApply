from __future__ import annotations

import re

STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "with", "at",
    "by", "from", "as", "is", "are", "be", "our", "your", "you", "we", "they",
    "this", "that", "these", "those", "will", "can", "able", "into", "about",
    "over", "under", "using", "use", "used", "via", "per", "than", "then",
    "such", "their", "its", "it", "not", "but", "if", "who", "what", "when",
    "where", "how", "all", "any", "each", "other", "also", "more", "most",
    "work", "working", "role", "team", "teams", "experience", "years", "year",
    "including", "across", "within", "strong", "ability", "skills", "skill",
    "requirements", "required", "preferred", "plus", "etc", "job", "position",
}

ALIASES = {
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    "k8s": "kubernetes",
    "reactjs": "react",
    "nodejs": "node",
    "node.js": "node",
    "golang": "go",
    "postgres": "postgresql",
    "tf": "terraform",
    "sklearn": "scikit-learn",
    "ci": "cicd",
    "cd": "cicd",
}

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+.#-]{1,}")
DIGIT_RE = re.compile(r"\d[\d,./]*\s*%?")


def normalize_token(token: str) -> str:
    lowered = token.lower().strip(".")
    return ALIASES.get(lowered, lowered)


def tokens(text: str) -> list[str]:
    found = []
    for raw in TOKEN_RE.findall(text or ""):
        token = normalize_token(raw)
        if token in STOPWORDS or len(token) < 2:
            continue
        if token.isdigit():
            continue
        found.append(token)
    return found


def unique(items: list[str]) -> list[str]:
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def digit_chunks(text: str) -> set[str]:
    return {re.sub(r"\s+", "", chunk) for chunk in DIGIT_RE.findall(text or "")}


def clip(text: str, limit: int) -> str:
    cleaned = (text or "").replace("\x00", "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
