from __future__ import annotations

import re
from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

SECTION_NAMES = {
    "experience": "experience",
    "work experience": "experience",
    "professional experience": "experience",
    "employment": "experience",
    "work history": "experience",
    "professional history": "experience",
    "education": "education",
    "academic background": "education",
    "academics": "education",
    "educational background": "education",
    "education and training": "education",
    "academic history": "education",
    "skills": "skills",
    "technical skills": "skills",
    "core skills": "skills",
    "summary": "summary",
    "professional summary": "summary",
    "profile": "summary",
    "objective": "summary",
    "projects": "projects",
    "selected projects": "projects",
    "certifications": "certifications",
    "awards": "awards",
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}")
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
DATE_TOKEN = rf"(?:{MONTH}\.?\s+)?(?:19|20)\d{{2}}|(?:0?[1-9]|1[0-2])[/-](?:19|20)\d{{2}}"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>{DATE_TOKEN})\s*(?:-|–|—|to)\s*(?P<end>present|current|now|expected\s+{DATE_TOKEN}|{DATE_TOKEN})",
    re.IGNORECASE,
)
DEGREE_RE = re.compile(
    r"\b(?:bachelor|master|doctor|ph\.?\s?d|b\.?\s?s\.?|m\.?\s?s\.?|b\.?\s?a\.?|m\.?\s?a\.?|"
    r"b\.?\s?tech|m\.?\s?tech|b\.?\s?e\.?|m\.?\s?e\.?|mba|associate|diploma|degree)\b",
    re.IGNORECASE,
)
DETAIL_RE = re.compile(
    r"\b(?:gpa|grade\s+point|coursework|relevant\s+courses|honou?rs|dean'?s\s+list|thesis|activities)\b",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


class PdfExtractError(Exception):
    def __init__(self, message: str, recovery: str):
        super().__init__(message)
        self.message = message
        self.recovery = recovery


def extract_pdf(data: bytes) -> dict:
    if not data:
        raise PdfExtractError(
            "The uploaded file is empty.",
            "Upload a text-based resume PDF, or enter experience manually.",
        )
    if not data.startswith(b"%PDF"):
        raise PdfExtractError(
            "This file is not a readable PDF.",
            "Upload a text-based resume PDF, or enter experience manually.",
        )
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise PdfExtractError(
            "This PDF is malformed and could not be read.",
            "Upload a different text-based PDF, or enter experience manually.",
        ) from exc
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception as exc:
            raise PdfExtractError(
                "This PDF is encrypted and could not be unlocked.",
                "Upload an unlocked PDF, or enter experience manually.",
            ) from exc
        if not unlocked:
            raise PdfExtractError(
                "This PDF is encrypted.",
                "Upload an unlocked PDF, or enter experience manually.",
            )
    pages = []
    try:
        for page in reader.pages:
            pages.append(page.extract_text() or "")
    except Exception as exc:
        raise PdfExtractError(
            "Text could not be extracted from this PDF.",
            "Upload a text-based PDF, or enter experience manually. Scanned resumes need OCR, which is not part of this release.",
        ) from exc
    text = "\n".join(pages).strip()
    letters = re.sub(r"\s+", "", text)
    if len(letters) < 40:
        raise PdfExtractError(
            "No extractable text was found. The PDF may be scanned, image-only, or empty.",
            "Upload a text-based PDF, or enter experience manually. OCR is not part of this release.",
        )
    parsed = parse_resume_text(text)
    parsed["page_count"] = len(reader.pages)
    return parsed


def parse_resume_text(text: str) -> dict:
    sections = _split_sections(text)
    contact = _contact(sections.get("preamble", ""), text)
    experience, experience_status = _parse_experience(sections.get("experience", ""))
    education = _parse_education(sections.get("education", ""))
    other = []
    for name, body in sections.items():
        if name in {"preamble", "experience", "education", "skills", "summary"} or not body:
            continue
        other.append({"title": name.replace("_", " ").title(), "text": body, "include": False})
    education_note = _education_note(sections.get("education", ""), education)
    if not sections.get("experience"):
        status = "missing_experience"
        message = (
            "No experience section was found. Review any extracted contact or education, "
            "then add experience before generating a resume. "
            + education_note
        )
    elif experience_status == "unparsed":
        status = "unparsed_experience"
        message = (
            "An experience section was found, but individual roles could not be parsed. "
            "Enter employers, titles, dates, and descriptions below. Nothing was imported as a completed role. "
            + education_note
        )
    else:
        status = "ok"
        message = "Review the extracted experience and education before continuing. " + education_note
    return {
        "status": status,
        "message": message,
        "contact": contact,
        "experience": experience,
        "education": education,
        "skills_text": sections.get("skills", ""),
        "summary_text": sections.get("summary", ""),
        "other_sections": other,
        "experience_source": sections.get("experience", ""),
        "raw_text": text,
    }


def _split_sections(text: str) -> dict[str, str]:
    buckets: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    for line in text.splitlines():
        kind = _header_kind(line.strip())
        if kind:
            current = kind
            buckets.setdefault(current, [])
            continue
        buckets.setdefault(current, []).append(line)
    return {key: "\n".join(value).strip() for key, value in buckets.items()}


def _header_kind(line: str) -> str | None:
    if not line or len(line) > 60:
        return None
    key = re.sub(r"[^a-zA-Z &/-]", "", line).strip().lower()
    key = re.sub(r"\s+", " ", key)
    if key in SECTION_NAMES:
        return SECTION_NAMES[key]
    if key.startswith("education") and len(key) <= 40:
        return "education"
    return None


def _contact(preamble: str, full_text: str) -> dict:
    email = EMAIL_RE.search(preamble) or EMAIL_RE.search(full_text)
    phone = PHONE_RE.search(preamble) or PHONE_RE.search(full_text)
    urls = URL_RE.findall(preamble) or URL_RE.findall(full_text)
    name = ""
    for line in preamble.splitlines():
        candidate = line.strip()
        if not candidate or EMAIL_RE.search(candidate) or PHONE_RE.search(candidate):
            continue
        if URL_RE.search(candidate) or _header_kind(candidate):
            continue
        words = candidate.split()
        if 1 < len(words) <= 5 and any(ch.isalpha() for ch in candidate) and len(candidate) <= 60:
            name = candidate
            break
    return {
        "name": name,
        "email": email.group(0) if email else "",
        "phone": phone.group(0) if phone else "",
        "location": "",
        "links": urls[:4],
    }


def _parse_experience(section: str) -> tuple[list[dict], str]:
    if not section.strip():
        return [], "missing"
    lines = [line.strip() for line in section.splitlines()]
    date_indexes = [index for index, line in enumerate(lines) if line and DATE_RANGE_RE.search(line)]
    if not date_indexes:
        return [], "unparsed"
    entries = []
    for position, index in enumerate(date_indexes):
        previous = date_indexes[position - 1] if position else -1
        header = _header_before(lines, index, previous)
        match = DATE_RANGE_RE.search(lines[index])
        assert match is not None
        remainder = f"{lines[index][: match.start()]} {lines[index][match.end() :]}"
        remainder = remainder.strip(" |-–—,;")
        title, employer = _split_role(header, remainder)
        body_end = _body_end(lines, index, date_indexes[position + 1] if position + 1 < len(date_indexes) else len(lines))
        body = "\n".join(line for line in lines[index + 1 : body_end] if line).strip()
        entries.append(
            {
                "employer": employer,
                "title": title,
                "start": match.group("start"),
                "end": match.group("end"),
                "description": body,
            }
        )
    return entries, "ok"


def _header_before(lines: list[str], index: int, previous: int) -> list[str]:
    collected = []
    cursor = index - 1
    while cursor > previous and len(collected) < 2:
        line = lines[cursor]
        if not line:
            if collected:
                break
            cursor -= 1
            continue
        if line.startswith(("-", "•", "*")):
            break
        collected.append(line)
        cursor -= 1
    collected.reverse()
    return collected


def _body_end(lines: list[str], index: int, next_index: int) -> int:
    if next_index >= len(lines):
        return len(lines)
    cursor = next_index - 1
    skipped = 0
    while cursor > index and skipped < 2:
        line = lines[cursor]
        if not line:
            cursor -= 1
            continue
        if line.startswith(("-", "•", "*")):
            break
        skipped += 1
        cursor -= 1
    return cursor + 1


def _split_role(header: list[str], remainder: str) -> tuple[str, str]:
    combined = remainder
    if not combined and header:
        combined = header[-1]
        header = header[:-1]
    title = ""
    employer = header[0] if header else ""
    if combined:
        parts = re.split(r"\s+(?:at|@)\s+|,\s+|\s+[|]\s+|\s+[-–—]\s+", combined, maxsplit=1)
        if len(parts) == 2:
            title, extra = parts[0].strip(), parts[1].strip()
            employer = employer or extra
        else:
            title = combined.strip()
    if not title and len(header) >= 1 and not employer:
        title = header[0]
    elif not title and len(header) >= 2:
        title = header[1]
    return title.strip(" |-–—,"), employer.strip(" |-–—,")


def _parse_education(section: str) -> list[dict]:
    lines = [line.strip(" -|") for line in section.splitlines() if line.strip()]
    if not lines:
        return []
    date_indexes = [index for index, line in enumerate(lines) if _education_date(line)]
    if not date_indexes:
        return _education_without_dates(lines)
    entries = []
    for position, index in enumerate(date_indexes):
        previous = date_indexes[position - 1] if position else -1
        next_index = date_indexes[position + 1] if position + 1 < len(date_indexes) else len(lines)
        header = _education_header_before(lines, index, previous)
        start, end, remainder = _education_date(lines[index])
        school, credential, extra = _split_education(header, remainder)
        body_end = _education_body_end(lines, index, next_index)
        detail_lines = [line for line in lines[index + 1 : body_end] if line]
        if extra:
            detail_lines.insert(0, extra)
        entries.append(
            {
                "school": school,
                "credential": credential,
                "start": start,
                "end": end,
                "details": "\n".join(detail_lines).strip(),
            }
        )
    return [entry for entry in entries if any(entry.values())]


def _education_without_dates(lines: list[str]) -> list[dict]:
    groups: list[list[str]] = [[]]
    for line in lines:
        if groups[-1] and DEGREE_RE.search(line) and any(DEGREE_RE.search(item) or DETAIL_RE.search(item) for item in groups[-1]):
            groups.append([line])
        else:
            groups[-1].append(line)
    entries = [_entry_from_lines(group) for group in groups if group]
    if entries:
        return entries
    return [{"school": lines[0], "credential": "", "start": "", "end": "", "details": "\n".join(lines[1:])}]


def _entry_from_lines(lines: list[str]) -> dict:
    school = ""
    credential = ""
    details = []
    for line in lines:
        if not credential and DEGREE_RE.search(line) and not DETAIL_RE.search(line):
            credential = line
        elif not school and not DETAIL_RE.search(line):
            school = line
        else:
            details.append(line)
    if not school and lines:
        school = lines[0]
    return {
        "school": school,
        "credential": credential,
        "start": "",
        "end": "",
        "details": "\n".join(details).strip(),
    }


def _education_header_before(lines: list[str], index: int, previous: int) -> list[str]:
    collected = []
    cursor = index - 1
    while cursor > previous and len(collected) < 2:
        line = lines[cursor]
        if not line or DETAIL_RE.search(line) or line.startswith(("-", "•", "*")):
            break
        collected.append(line)
        cursor -= 1
    collected.reverse()
    return collected


def _education_body_end(lines: list[str], index: int, next_index: int) -> int:
    if next_index >= len(lines):
        return len(lines)
    cursor = next_index - 1
    skipped = 0
    while cursor > index and skipped < 2:
        line = lines[cursor]
        if not line or DETAIL_RE.search(line) or line.startswith(("-", "•", "*")):
            break
        skipped += 1
        cursor -= 1
    return cursor + 1


def _education_date(line: str) -> tuple[str, str, str] | None:
    match = DATE_RANGE_RE.search(line)
    if match:
        remainder = f"{line[: match.start()]} {line[match.end() :]}".strip(" |-–—,;")
        return match.group("start"), match.group("end"), remainder
    years = YEAR_RE.findall(line)
    if len(years) == 1 and len(line) <= 48 and not DETAIL_RE.search(line):
        remainder = YEAR_RE.sub("", line).strip(" |-–—,;")
        return years[0], years[0], remainder
    return None


def _split_education(header: list[str], remainder: str) -> tuple[str, str, str]:
    parts = [part.strip(" |-–—,") for part in header if part.strip(" |-–—,")]
    if remainder:
        pieces = [piece.strip() for piece in re.split(r"\s*[|,]\s+|\s+[-–—]\s+", remainder) if piece.strip()]
        parts.extend(pieces)
    school = ""
    credential = ""
    extra = []
    for part in parts:
        if DETAIL_RE.search(part):
            extra.append(part)
        elif not credential and DEGREE_RE.search(part):
            credential = part
        elif not school:
            school = part
        elif not credential:
            credential = part
        else:
            extra.append(part)
    if not school and credential and "," in credential:
        left, right = [piece.strip() for piece in credential.split(",", 1)]
        if DEGREE_RE.search(left):
            credential, school = left, right
        else:
            school, credential = left, right
    elif not school and credential:
        school, credential = credential, ""
    return school, credential, "\n".join(extra)


def _education_note(section: str, education: list[dict]) -> str:
    if not section.strip():
        return "No education section was found. Add school, credential, dates, and details if they should appear."
    if not education:
        return "An education section was found, but its details could not be split. Enter them below."
    count = len(education)
    noun = "entry" if count == 1 else "entries"
    return f"Extracted {count} education {noun}, including school, credential, dates, and details such as GPA or coursework."
