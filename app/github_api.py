from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from app.project_map import describe_project, pick_source_paths
from app.relevance import filter_repos

API = "https://api.github.com"
USER_AGENT = "ImposterApply"


class GitHubError(Exception):
    def __init__(self, message: str, recovery: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.recovery = recovery
        self.status = status


class GitHubService:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        kwargs = {"timeout": 20.0, "transport": self._transport}
        if self._transport is None:
            kwargs.pop("transport")
        return httpx.AsyncClient(**kwargs)

    async def list_repositories(self, *, token: str, username: str, visibility: str) -> dict:
        if visibility not in {"public", "private", "both"}:
            raise GitHubError(
                "Repository visibility must be public, private, or both.",
                "Choose one of those options and list repositories again.",
                400,
            )
        if visibility != "public" and not token:
            raise GitHubError(
                "Private repositories require authorization.",
                "Paste a personal access token with repo scope, connect GitHub OAuth, or switch to public-only.",
                401,
            )
        if not token and not username:
            raise GitHubError(
                "A GitHub username is required for public repositories.",
                "Enter the account whose public repositories should be listed.",
                400,
            )
        async with self._client() as client:
            if token and visibility != "public":
                login = await self._authenticated_login(client, token)
                raw = await self._paginated(
                    client,
                    f"{API}/user/repos",
                    token,
                    {"visibility": {"public": "public", "private": "private", "both": "all"}[visibility], "affiliation": "owner,collaborator,organization_member", "per_page": "100"},
                )
                warning = ""
                if visibility == "private" and not any(item.get("private") for item in raw):
                    warning = (
                        "No private repositories were returned. The token may lack the repo scope, "
                        "or this account has no private repositories."
                    )
                return {
                    "repos": filter_repos([_repo(item) for item in raw], visibility),
                    "login": login,
                    "warning": warning,
                }
            login = await self._public_login(client, username, token)
            raw = await self._paginated(
                client,
                f"{API}/users/{quote(username)}/repos",
                token,
                {"type": "owner", "per_page": "100"},
            )
            return {
                "repos": filter_repos([_repo(item) for item in raw], "public"),
                "login": login,
                "warning": "",
            }

    async def build_map(self, *, token: str, repos: list[dict]) -> list[dict]:
        semaphore = asyncio.Semaphore(3)

        async with self._client() as client:
            async def one(repo: dict) -> dict:
                async with semaphore:
                    skim = await self._skim(client, token, repo)
                    summary = describe_project(
                        repo_description=repo.get("description") or "",
                        readme=skim["readme"],
                        source_files=skim["source_files"],
                        languages=skim["languages"],
                    )
                    return {
                        **repo,
                        "summary": summary,
                        "readme": skim["readme"][:4000],
                        "languages": skim["languages"],
                        "files_reviewed": [item["path"] for item in skim["source_files"]],
                        "source_excerpts": [item["text"][:240] for item in skim["source_files"]],
                        "topics": repo.get("topics") or [],
                    }

            return list(await asyncio.gather(*(one(repo) for repo in repos)))

    async def login_for_token(self, token: str) -> str:
        async with self._client() as client:
            return await self._authenticated_login(client, token)

    async def exchange_code(self, *, client_id: str, client_secret: str, code: str, redirect_uri: str) -> str:
        async with self._client() as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
                headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            )
        payload = response.json() if response.content else {}
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code >= 400 or not token:
            raise GitHubError(
                "GitHub did not return an access token.",
                "Try the connection again, or paste a personal access token. Your other inputs are saved.",
                401,
            )
        return token

    async def _authenticated_login(self, client: httpx.AsyncClient, token: str) -> str:
        response = await self._request(client, "GET", f"{API}/user", token)
        payload = response.json()
        login = payload.get("login") if isinstance(payload, dict) else ""
        if not login:
            raise GitHubError(
                "GitHub did not identify this token.",
                "Create a new personal access token and connect again. Your resume inputs are saved.",
                401,
            )
        return login

    async def _public_login(self, client: httpx.AsyncClient, username: str, token: str = "") -> str:
        response = await self._request(client, "GET", f"{API}/users/{quote(username)}", token)
        payload = response.json()
        login = payload.get("login") if isinstance(payload, dict) else ""
        if not login:
            raise GitHubError(
                f"GitHub user {username} was not found.",
                "Check the username and try again. Your resume inputs are saved.",
                404,
            )
        return login

    async def _paginated(self, client, url: str, token: str, params: dict) -> list[dict]:
        collected = []
        next_url = url
        next_params = params
        for _ in range(10):
            response = await self._request(client, "GET", next_url, token, params=next_params)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError(
                    "GitHub returned an unexpected repository list.",
                    "Retry listing repositories. Your inputs are saved.",
                    502,
                )
            collected.extend(item for item in payload if isinstance(item, dict))
            link = response.headers.get("link", "")
            match = None
            for part in link.split(","):
                if 'rel="next"' in part:
                    match = part
                    break
            if not match or len(payload) < 100:
                break
            next_url = match[match.find("<") + 1 : match.find(">")]
            next_params = None
        return collected

    async def _skim(self, client, token: str, repo: dict) -> dict:
        full_name = quote(repo["full_name"], safe="/")
        readme = await self._optional_text(
            client,
            f"{API}/repos/{full_name}/readme",
            token,
            {"Accept": "application/vnd.github.raw"},
        )
        languages_payload = await self._optional_json(client, f"{API}/repos/{full_name}/languages", token)
        languages = list(languages_payload) if isinstance(languages_payload, dict) else []
        if repo.get("language") and repo["language"] not in languages:
            languages.insert(0, repo["language"])
        branch = quote(repo.get("default_branch") or "HEAD", safe="")
        tree = await self._optional_json(client, f"{API}/repos/{full_name}/git/trees/{branch}?recursive=1", token)
        entries = tree.get("tree") if isinstance(tree, dict) else []
        paths = pick_source_paths(entries if isinstance(entries, list) else [])
        source_files = []
        for path in paths:
            text = await self._optional_text(
                client,
                f"{API}/repos/{full_name}/contents/{quote(path, safe='/')}",
                token,
                {"Accept": "application/vnd.github.raw"},
            )
            if text:
                source_files.append({"path": path, "text": text[:4000]})
        return {"readme": (readme or "")[:8000], "languages": languages, "source_files": source_files}

    async def _optional_text(self, client, url: str, token: str, extra_headers: dict | None = None) -> str:
        try:
            response = await self._request(client, "GET", url, token, extra_headers=extra_headers)
        except GitHubError as exc:
            if exc.status in {401, 429}:
                raise
            return ""
        if response.status_code == 404:
            return ""
        return response.text or ""

    async def _optional_json(self, client, url: str, token: str):
        try:
            response = await self._request(client, "GET", url, token)
        except GitHubError as exc:
            if exc.status in {401, 429}:
                raise
            return None
        if response.status_code == 404:
            return None
        return response.json()

    async def _request(self, client, method, url, token, params=None, extra_headers=None):
        headers = {"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT, "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if extra_headers:
            headers.update(extra_headers)
        response = await client.request(method, url, headers=headers, params=params)
        if response.status_code == 401:
            raise GitHubError(
                "GitHub rejected this authorization.",
                "The token may be expired or missing required scope. Connect again, or continue with public repositories. Your inputs are saved.",
                401,
            )
        if response.status_code == 403 and _rate_limited(response):
            reset = _reset_text(response.headers.get("x-ratelimit-reset", ""))
            raise GitHubError(
                "GitHub rate limit reached.",
                f"Paste a personal access token in the field above, or retry after {reset}. Your inputs and edits are saved.",
                429,
            )
        if response.status_code == 404 and url.rstrip("/").endswith("/user"):
            raise GitHubError(
                "GitHub could not access this account.",
                "Check the username or token and retry. Your inputs are saved.",
                404,
            )
        if response.status_code >= 500:
            raise GitHubError(
                "GitHub is unavailable.",
                "Retry in a moment. Your inputs and edits are saved.",
                502,
            )
        if response.status_code >= 400 and response.status_code != 404:
            raise GitHubError(
                "GitHub could not complete this request.",
                "Check repository access and retry. Excluded repositories were not analyzed. Your inputs are saved.",
                response.status_code,
            )
        return response


def _reset_text(reset: str) -> str:
    try:
        moment = datetime.fromtimestamp(int(reset), UTC)
    except (TypeError, ValueError):
        return "the reset time"
    return moment.strftime("%H:%M UTC on %d %b %Y")


def _rate_limited(response: httpx.Response) -> bool:
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining == "0":
        return True
    body = (response.text or "").lower()
    return "rate limit" in body


def _repo(item: dict) -> dict:
    owner = item.get("owner") or {}
    name = item.get("name") or ""
    full_name = item.get("full_name") or (f"{owner.get('login')}/{name}" if owner.get("login") and name else name)
    return {
        "full_name": full_name,
        "private": bool(item.get("private")),
        "fork": bool(item.get("fork")),
        "html_url": item.get("html_url") or "",
        "description": item.get("description") or "",
        "language": item.get("language") or "",
        "topics": item.get("topics") or [],
        "default_branch": item.get("default_branch") or "HEAD",
        "pushed_at": item.get("pushed_at") or "",
    }
