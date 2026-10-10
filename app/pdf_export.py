from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.resume_build import project_export_text

SERIF = Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf")
SERIF_BOLD = Path("/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf")
SANS = Path("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf")
_FONTS_READY = False


def render_resume_pdf(draft: dict) -> bytes:
    _register_fonts()
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.65 * inch,
        bottomMargin=0.65 * inch,
        title=(draft.get("name") or "Resume") + " resume",
        author=draft.get("name") or "",
    )
    styles = _styles()
    story = []
    if draft.get("name"):
        story.append(Paragraph(_escape(draft["name"]), styles["name"]))
    if draft.get("contact_line"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(_escape(draft["contact_line"]), styles["contact"]))
    _section(story, styles, "Summary", draft.get("summary") or "")
    if draft.get("experience"):
        _append_entries(story, styles, "Experience", draft["experience"], _experience_text)
    if draft.get("education"):
        _append_entries(story, styles, "Education", draft["education"], _education_text)
    if (draft.get("skills") or "").strip():
        _section(story, styles, "Skills", draft["skills"])
    exported_projects = [project_export_text(project) for project in draft.get("projects") or []]
    exported_projects = [text for text in exported_projects if text.strip()]
    if exported_projects:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Projects", styles["section"]))
        for text in exported_projects:
            story.append(Spacer(1, 6))
            story.append(Paragraph(_multiline(text), styles["body"]))
    if (draft.get("additional") or "").strip():
        _section(story, styles, "Additional", draft["additional"])
    if not story:
        story.append(Paragraph("Resume draft is empty.", styles["body"]))
    document.build(story)
    return buffer.getvalue()


def _append_entries(story, styles, title, entries, formatter):
    story.append(Spacer(1, 10))
    story.append(Paragraph(title, styles["section"]))
    for entry in entries:
        text = formatter(entry)
        if not text.strip():
            continue
        story.append(Spacer(1, 6))
        story.append(Paragraph(_multiline(text), styles["body"]))


def _experience_text(entry: dict) -> str:
    heading = " · ".join(part for part in [entry.get("title") or "", entry.get("employer") or ""] if part)
    return "\n".join(part for part in [heading, entry.get("dates") or "", entry.get("description") or ""] if part)


def _education_text(entry: dict) -> str:
    heading = " · ".join(
        part for part in [entry.get("credential") or "", entry.get("school") or ""] if part
    )
    return "\n".join(part for part in [heading, entry.get("dates") or "", entry.get("details") or ""] if part)


def _section(story, styles, title, body):
    if not (body or "").strip():
        return
    if title:
        story.append(Spacer(1, 10))
        story.append(Paragraph(title, styles["section"]))
        story.append(Spacer(1, 4))
    story.append(Paragraph(_multiline(body), styles["body"]))


def _styles():
    font, bold, sans = _font_names()
    return {
        "name": ParagraphStyle("Name", fontName=bold, fontSize=16, leading=19, textColor="#1A1814"),
        "contact": ParagraphStyle("Contact", fontName=sans, fontSize=9, leading=12, textColor="#3F3A33"),
        "section": ParagraphStyle("Section", fontName=bold, fontSize=11, leading=14, textColor="#9A3412", spaceBefore=2),
        "body": ParagraphStyle("Body", fontName=font, fontSize=10, leading=13, textColor="#1A1814"),
    }


def _font_names() -> tuple[str, str, str]:
    if _FONTS_READY:
        return "ResumeSerif", "ResumeSerifBold", "ResumeSans"
    return "Times-Roman", "Times-Bold", "Times-Roman"


def _register_fonts() -> None:
    global _FONTS_READY
    if _FONTS_READY or not SERIF.exists():
        return
    pdfmetrics.registerFont(TTFont("ResumeSerif", str(SERIF)))
    pdfmetrics.registerFont(TTFont("ResumeSerifBold", str(SERIF_BOLD)))
    pdfmetrics.registerFont(TTFont("ResumeSans", str(SANS)))
    _FONTS_READY = True


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _multiline(text: str) -> str:
    escaped = _escape(_break_long_words(text or ""))
    return escaped.replace("\n", "<br/>")


def _break_long_words(text: str) -> str:
    def shorten(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) <= 40:
            return word
        return " ".join(word[index : index + 40] for index in range(0, len(word), 40))

    return re.sub(r"\S{41,}", shorten, text)
