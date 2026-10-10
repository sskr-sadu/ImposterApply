from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.github_api import GitHubError, GitHubService
from app.pdf_export import render_resume_pdf
from app.pdf_extract import PdfExtractError, extract_pdf
from app.project_map import suggest_projects
from app.relevance import SelectionError, select_repos
from app.resume_build import assert_grounded, build_draft, source_text_for_digits
from app.settings import Settings
from app.store import Store, empty_session
from app.textutil import clip, digit_chunks

STATIC = Path(__file__).resolve().parent / "static"
COOKIE = "ia_session"
MAX_PDF = 8 * 1024 * 1024


def create_app(settings: Settings | None = None, github: GitHubService | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="ImposterApply", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.store = Store(settings.data_dir)
    app.state.github = github or GitHubService()
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def attach_session(request: Request, call_next):
        created = False
        session_id = request.cookies.get(COOKIE)
        if not session_id:
            session_id = secrets.token_urlsafe(24)
            created = True
        request.state.session_id = session_id
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static"):
            response.headers["Cache-Control"] = "no-store"
        if created:
            secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
            response.set_cookie(COOKIE, session_id, httponly=True, samesite="lax", path="/", secure=secure)
        return response

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"oauth_available": settings.oauth_configured}

    @app.get("/api/session")
    def read_session(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        return _public(app.state.store.get(session_id))

    @app.post("/api/intake")
    async def intake(
        request: Request,
        response: Response,
        job_description: str = Form(""),
        resume: UploadFile | None = File(None),  # noqa: B008
    ):
        session_id = _ensure_session(request, response)
        state = app.state.store.get(session_id)
        if not job_description.strip():
            _fail(400, "Job description is empty.", "Paste the role description and submit again. Nothing was imported.")
        state["job_description"] = job_description.strip()
        if resume is None:
            state["extraction_status"] = "manual"
            state["extraction_message"] = "No PDF was uploaded. Enter experience manually."
            app.state.store.save(session_id, state)
            _fail(400, "A resume PDF is required, or choose manual entry.", "Upload a text-based PDF, or continue and type experience yourself.")
        filename = resume.filename or "resume.pdf"
        if not filename.lower().endswith(".pdf"):
            app.state.store.save(session_id, state)
            _fail(400, "The uploaded file is not a PDF.", "Upload a text-based resume PDF, or enter experience manually. The job description was saved.")
        data = await resume.read()
        if len(data) > MAX_PDF:
            app.state.store.save(session_id, state)
            _fail(400, "The PDF is larger than 8 MB.", "Upload a smaller text-based PDF, or enter experience manually. The job description was saved.")
        try:
            parsed = extract_pdf(data)
        except PdfExtractError as exc:
            state["pdf_name"] = filename
            state["extraction_status"] = "failed"
            state["extraction_message"] = exc.message
            state["experience"] = []
            app.state.store.save(session_id, state)
            _fail(422, exc.message, exc.recovery + " The job description was saved.")
        _apply_extraction(state, filename, parsed)
        app.state.store.save(session_id, state)
        return _public(state)

    @app.put("/api/review")
    async def save_review(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        if not state.get("job_description"):
            _fail(400, "Add a job description before reviewing experience.", "Go back to the first step and paste the role.")
        state["contact"] = _contact(body.get("contact") or {})
        state["experience"] = [_experience(item) for item in body.get("experience") or []]
        state["education"] = [_education(item) for item in body.get("education") or []]
        state["skills_text"] = str(body.get("skills_text") or "")
        state["summary_text"] = str(body.get("summary_text") or "")
        state["other_sections"] = [
            {
                "title": str(item.get("title") or "Section")[:80],
                "text": str(item.get("text") or ""),
                "include": bool(item.get("include")),
            }
            for item in body.get("other_sections") or []
        ]
        state["extraction_status"] = state.get("extraction_status") or "manual"
        app.state.store.save(session_id, state)
        return _public(state)

    @app.post("/api/github/connect")
    async def connect_github(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        visibility = body.get("visibility") or "public"
        if visibility not in {"public", "private", "both"}:
            _fail(400, "Choose public, private, or both.", "Public-only is the default and does not read private repositories.")
        username = str(body.get("username") or "").strip().lstrip("@")
        token = str(body.get("token") or "").strip() or state["github"].get("token") or ""
        if not username and not token:
            _fail(400, "A GitHub username is required.", "Enter the GitHub user ID whose repositories should be mapped.")
        if body.get("clear_token"):
            token = ""
        try:
            listed = await app.state.github.list_repositories(
                token=token, username=username, visibility=visibility
            )
        except GitHubError as exc:
            state["github"]["visibility"] = visibility
            state["github"]["username"] = username or state["github"].get("username") or ""
            app.state.store.save(session_id, state)
            _fail(exc.status, exc.message, exc.recovery)
        state["github"].update(
            {
                "visibility": visibility,
                "username": listed["login"] or username,
                "token": token,
                "auth_method": "token" if token else "public",
                "oauth_state": "",
            }
        )
        if not (listed["login"] or username):
            _fail(400, "A GitHub username is required.", "Enter the GitHub user ID whose repositories should be mapped.")
        repos = sorted(listed["repos"], key=lambda repo: repo.get("pushed_at") or "", reverse=True)
        try:
            project_map = await app.state.github.build_map(token=token, repos=repos)
        except GitHubError as exc:
            state["eligible_repos"] = repos
            state["github_warning"] = listed.get("warning") or ""
            app.state.store.save(session_id, state)
            _fail(exc.status, exc.message, exc.recovery)
        warning = listed.get("warning") or ""
        if not project_map:
            warning = (warning + " " if warning else "") + "No repositories were found for this user."
        state["github_warning"] = warning.strip()
        state["eligible_repos"] = repos
        state["project_map"] = project_map
        state["suggestions"] = []
        state["other_projects"] = []
        state["recommendations"] = project_map
        app.state.store.save(session_id, state)
        return _public(state)

    @app.get("/api/github/oauth/start")
    def oauth_start(request: Request, response: Response):
        if not settings.oauth_configured:
            _fail(
                400,
                "GitHub OAuth is not configured.",
                "Set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET, or paste a personal access token.",
            )
        session_id = _ensure_session(request, response)
        state = app.state.store.get(session_id)
        oauth_state = secrets.token_urlsafe(24)
        state["github"]["oauth_state"] = oauth_state
        app.state.store.save(session_id, state)
        callback = settings.oauth_callback or str(request.url_for("oauth_callback"))
        query = urlencode(
            {
                "client_id": settings.github_client_id,
                "scope": "repo",
                "state": oauth_state,
                "redirect_uri": callback,
            }
        )
        return RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")

    @app.get("/api/github/oauth/callback")
    async def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        session_id = request.cookies.get(COOKIE)
        if not session_id:
            return RedirectResponse("/?github=missing-session")
        payload = app.state.store.get(session_id)
        if error or not code or state != payload["github"].get("oauth_state"):
            payload["github_warning"] = "GitHub authorization was denied or expired. You can retry or use a token."
            app.state.store.save(session_id, payload)
            return RedirectResponse("/?github=denied")
        callback = settings.oauth_callback or str(request.url_for("oauth_callback"))
        try:
            token = await app.state.github.exchange_code(
                client_id=settings.github_client_id,
                client_secret=settings.github_client_secret,
                code=code,
                redirect_uri=callback,
            )
            login = await app.state.github.login_for_token(token)
        except GitHubError as exc:
            payload["github_warning"] = exc.message
            app.state.store.save(session_id, payload)
            return RedirectResponse("/?github=failed")
        payload["github"].update(
            {
                "username": login,
                "token": token,
                "auth_method": "oauth",
                "oauth_state": "",
            }
        )
        payload["github_warning"] = "GitHub connected. Choose repository visibility, then list repositories."
        app.state.store.save(session_id, payload)
        return RedirectResponse("/?github=connected")

    @app.post("/api/github/analyze")
    async def analyze(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        names = list(body.get("repositories") or [])
        if not names:
            _fail(400, "No repositories were selected.", "Select at least one repository, then analyze again. Nothing was read.")
        if len(names) > 20:
            _fail(
                400,
                "Select 20 or fewer repositories.",
                "Analyze a smaller set so GitHub is not rate limited. Your other inputs are saved.",
            )
        try:
            chosen = select_repos(names, state.get("eligible_repos") or [])
        except SelectionError as exc:
            _fail(
                400,
                "Some repositories are outside the permitted set: " + ", ".join(exc.names),
                "List repositories again and select only eligible ones. Excluded repositories were not analyzed.",
            )
        try:
            project_map = await app.state.github.build_map(
                token=state["github"].get("token") or "",
                repos=chosen,
            )
        except GitHubError as exc:
            app.state.store.save(session_id, state)
            _fail(exc.status, exc.message, exc.recovery)
        state["project_map"] = project_map
        job = (state.get("job_description") or "").strip()
        if job:
            ranked = suggest_projects(job, project_map, limit=3)
            state["suggestions"] = ranked["suggestions"]
            state["other_projects"] = ranked["others"]
        else:
            state["suggestions"] = []
            state["other_projects"] = []
        state["recommendations"] = project_map
        state["github_warning"] = ""
        app.state.store.save(session_id, state)
        return _public(state)

    @app.post("/api/projects/suggest")
    async def suggest(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        job = str(body.get("job_description") or "").strip()
        if not job:
            _fail(400, "Job description is empty.", "Paste the role description. The project map was kept.")
        if not state.get("project_map"):
            _fail(400, "No project map exists yet.", "Connect GitHub first so repositories can be skimmed. Nothing outside that map is suggested.")
        state["job_description"] = job
        ranked = suggest_projects(job, state["project_map"], limit=3)
        state["suggestions"] = ranked["suggestions"]
        state["other_projects"] = ranked["others"]
        state["selected_projects"] = []
        app.state.store.save(session_id, state)
        return _public(state)

    @app.post("/api/projects/select")
    async def select_projects_route(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        try:
            chosen = _selected_from_map(state, body.get("selected") or [])
        except SelectionError as exc:
            _fail(
                400,
                "Some projects are outside the mapped set: " + ", ".join(exc.names),
                "Choose a suggested project or one from Other. Repositories that were not skimmed are not added.",
            )
        state["selected_projects"] = [item["full_name"] for item in chosen]
        app.state.store.save(session_id, state)
        return _public(state)

    @app.post("/api/resume/generate")
    async def generate(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        if not (state.get("job_description") or "").strip():
            _fail(400, "Job description is empty.", "Paste the role description. Existing edits were kept.")
        experience = state.get("experience") or []
        names = body.get("selected") if body.get("selected") is not None else state.get("selected_projects") or []
        try:
            selected = _selected_from_map(state, names)
        except SelectionError as exc:
            _fail(
                400,
                "Some projects are outside the mapped set: " + ", ".join(exc.names),
                "Choose a suggested project or one from Other. Repositories that were not skimmed are not added.",
            )
        if not experience and not selected and not body.get("allow_empty"):
            _fail(
                400,
                "There is no confirmed experience or selected project.",
                "Add experience, select a project, or explicitly continue with an empty draft. Nothing was invented.",
            )
        additional = state.get("summary_text") or ""
        for section in state.get("other_sections") or []:
            if section.get("include") and section.get("text"):
                additional = (additional + "\n" + section["text"]).strip()
        draft = build_draft(
            job_description=state["job_description"],
            contact=state.get("contact") or {},
            experience=experience,
            education=state.get("education") or [],
            skills_text=state.get("skills_text") or "",
            additional_text=additional,
            projects=selected,
        )
        allowed = digit_chunks(
            source_text_for_digits(
                experience=experience,
                education=state.get("education") or [],
                skills_text=state.get("skills_text") or "",
                additional_text=additional,
                projects=selected,
            )
        )
        allowed.update(digit_chunks(" ".join(entry.get("start", "") + entry.get("end", "") for entry in experience)))
        assert_grounded(draft, allowed)
        if not selected:
            draft["project_note"] = (
                "No relevant projects were selected. This draft uses confirmed experience only and does not add projects."
            )
        state["draft"] = draft
        app.state.store.save(session_id, state)
        return _public(state)

    @app.put("/api/resume")
    async def save_resume(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        state = app.state.store.get(session_id)
        if not state.get("draft"):
            _fail(400, "Generate a draft before editing it.", "Your inputs are saved. Generate the draft, then edit.")
        state["draft"] = _sanitize_draft(body.get("draft") or {}, state["draft"])
        app.state.store.save(session_id, state)
        return _public(state)

    @app.post("/api/resume/pdf")
    async def export_pdf(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        state = app.state.store.get(session_id)
        draft = state.get("draft")
        if not draft:
            _fail(400, "There is no draft to export.", "Generate a resume first. Your inputs are saved.")
        try:
            pdf = render_resume_pdf(draft)
        except Exception:  # noqa: BLE001
            _fail(500, "The PDF could not be created.", "Edit the draft and try the download again. Your edits are saved.")
        filename = _filename(draft.get("name") or "resume")
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/api/manual")
    async def start_manual(request: Request, response: Response):
        session_id = _ensure_session(request, response)
        body = await request.json()
        job = str(body.get("job_description") or "").strip()
        if not job:
            _fail(400, "Job description is empty.", "Paste the role description before entering experience manually.")
        state = app.state.store.get(session_id)
        state["job_description"] = job
        state["extraction_status"] = "manual"
        state["extraction_message"] = "Experience was not imported from a PDF. Enter it below."
        app.state.store.save(session_id, state)
        return _public(state)

    return app


def _apply_extraction(state: dict, filename: str, parsed: dict) -> None:
    state["pdf_name"] = filename
    state["extraction_status"] = parsed["status"]
    state["extraction_message"] = parsed["message"]
    state["contact"] = parsed["contact"]
    state["experience"] = parsed["experience"]
    state["education"] = parsed["education"]
    state["skills_text"] = parsed["skills_text"]
    state["summary_text"] = parsed["summary_text"]
    state["other_sections"] = parsed["other_sections"]
    state["experience_source"] = parsed["experience_source"]
    state["raw_text"] = clip(parsed.get("raw_text") or "", 20000)


def _selected_from_map(state: dict, selected_names: list[str]) -> list[dict]:
    chosen = select_repos(list(selected_names), state.get("project_map") or [])
    return [{**item, "selected": True, "description": item.get("summary") or ""} for item in chosen]


def _sanitize_draft(incoming: dict, previous: dict) -> dict:
    previous_projects = {item.get("full_name"): item for item in previous.get("projects") or []}
    projects = []
    for item in incoming.get("projects") or []:
        prior = previous_projects.get(item.get("full_name"), {})
        private = bool(prior.get("private", item.get("private")))
        projects.append(
            {
                "full_name": prior.get("full_name") or str(item.get("full_name") or ""),
                "private": private,
                "include_in_export": bool(item.get("include_in_export")),
                "include_link": bool(item.get("include_link")),
                "link": prior.get("link") or "",
                "heading": str(item.get("heading") or prior.get("heading") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    experience = []
    for item in incoming.get("experience") or []:
        experience.append(
            {
                "employer": str(item.get("employer") or ""),
                "title": str(item.get("title") or ""),
                "dates": str(item.get("dates") or ""),
                "description": str(item.get("description") or ""),
            }
        )
    education = []
    for item in incoming.get("education") or []:
        education.append(
            {
                "school": str(item.get("school") or ""),
                "credential": str(item.get("credential") or ""),
                "dates": str(item.get("dates") or ""),
                "details": str(item.get("details") or ""),
            }
        )
    return {
        "name": str(incoming.get("name") or ""),
        "contact_line": str(incoming.get("contact_line") or ""),
        "summary": str(incoming.get("summary") or ""),
        "experience": experience,
        "education": education,
        "skills": str(incoming.get("skills") or ""),
        "additional": str(incoming.get("additional") or ""),
        "projects": projects,
        "shared_terms": previous.get("shared_terms") or [],
        "project_note": previous.get("project_note") or "",
    }


def _contact(payload: dict) -> dict:
    links = payload.get("links") or []
    if isinstance(links, str):
        links = [line.strip() for line in links.splitlines() if line.strip()]
    return {
        "name": str(payload.get("name") or ""),
        "email": str(payload.get("email") or ""),
        "phone": str(payload.get("phone") or ""),
        "location": str(payload.get("location") or ""),
        "links": [str(link) for link in links][:6],
    }


def _experience(item: dict) -> dict:
    return {
        "employer": str(item.get("employer") or ""),
        "title": str(item.get("title") or ""),
        "start": str(item.get("start") or ""),
        "end": str(item.get("end") or ""),
        "description": str(item.get("description") or ""),
    }


def _education(item: dict) -> dict:
    return {
        "school": str(item.get("school") or ""),
        "credential": str(item.get("credential") or ""),
        "start": str(item.get("start") or ""),
        "end": str(item.get("end") or ""),
        "details": str(item.get("details") or ""),
    }


def _public(state: dict) -> dict:
    github = state.get("github") or empty_session()["github"]
    safe = {
        key: value
        for key, value in state.items()
        if key not in {"github", "project_map", "suggestions", "other_projects", "recommendations"}
    }
    safe["github"] = {
        "visibility": github.get("visibility") or "public",
        "username": github.get("username") or "",
        "connected": bool(github.get("token") or github.get("username")),
        "auth_method": github.get("auth_method") or "",
    }
    safe["raw_text"] = clip(state.get("raw_text") or state.get("experience_source") or "", 20000)
    if "experience_source" in safe:
        safe["experience_source"] = clip(safe.get("experience_source") or "", 20000)
    safe["project_map"] = [_public_project(item) for item in state.get("project_map") or []]
    safe["suggestions"] = [_public_project(item, include_why=True) for item in state.get("suggestions") or []]
    safe["other_projects"] = [_public_project(item, include_why=True) for item in state.get("other_projects") or []]
    safe["recommendations"] = [
        {
            "full_name": item.get("full_name") or "",
            "private": bool(item.get("private")),
            "description": item.get("summary") or "",
            "recommended": True,
            "selected": True,
            "evidence": [{"source": "map", "detail": item.get("summary") or ""}],
        }
        for item in state.get("recommendations") or state.get("project_map") or []
    ]
    repos = []
    for item in state.get("eligible_repos") or []:
        repos.append(
            {
                "full_name": item.get("full_name"),
                "private": bool(item.get("private")),
                "fork": bool(item.get("fork")),
                "description": item.get("description") or "",
                "language": item.get("language") or "",
                "html_url": "" if item.get("private") else item.get("html_url") or "",
            }
        )
    safe["eligible_repos"] = repos
    return safe


def _public_project(item: dict, include_why: bool = False) -> dict:
    payload = {
        "full_name": item.get("full_name") or "",
        "private": bool(item.get("private")),
        "summary": item.get("summary") or "",
        "language": item.get("language") or "",
        "languages": item.get("languages") or [],
        "files_reviewed": item.get("files_reviewed") or [],
        "html_url": "" if item.get("private") else item.get("html_url") or "",
    }
    if include_why:
        payload["why"] = item.get("why") or ""
        payload["overlap_terms"] = item.get("overlap_terms") or []
    return payload


def _ensure_session(request: Request, response: Response) -> str:
    return request.state.session_id


def _filename(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in name).strip("-")
    return (cleaned or "resume") + ".pdf"


def _fail(status: int, error: str, recovery: str) -> None:
    raise HTTPException(status_code=status, detail={"error": error, "recovery": recovery})


app = create_app()
