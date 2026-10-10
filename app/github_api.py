from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

from app.relevance import filter_repos, score_repository

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
            if token:
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
            login = await self._public_login(client, username)
            raw = await self._paginated(
                client,
                f"{API}/users/{quote(username)}/repos",
                "",
                {"type": "owner", "per_page": "100"},
            )
            return {
                "repos": filter_repos([_repo(item) for item in raw], "public"),
                "login": login,
                "warning": "",
            }

    async def analyze(self, *, token: str, username: str, job_description: str, repos: list[dict]) -> list[dict]:
        semaphore = asyncio.Semaphore(4)
        async with self._client() as client:
            async def one(repo: dict) -> dict:
                async with semaphore:
                    evidence = await self._evidence(client, token, repo)
                    scored = score_repository(job_description, evidence)
                    verified = await self._authored(client, token, repo["full_name"], username)
                    return {
                        **repo,
                        "languages": evidence["languages"],
                        "score": scored["score"],
                        "overlap_terms": scored["overlap_terms"],
                        "evidence": scored["evidence"],
                        "contribution_verified": verified,
                        "recommended": scored["recommended"],
                        "selected": scored["recommended"],
                    }

            results = await asyncio.gather(*(one(repo) for repo in repos))
        return sorted(results, key=lambda item: (-item["score"], item["full_name"]))

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

    async def _public_login(self, client: httpx.AsyncClient, username: str) -> str:
        response = await self._request(client, "GET", f"{API}/users/{quote(username)}", "")
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

    async def _evidence(self, client, token: str, repo: dict) -> dict:
        full_name = quote(repo["full_name"], safe="/")
        readme = await self._optional_text(
            client,
            f"{API}/repos/{full_name}/readme",
            token,
            {"Accept": "application/vnd.github.raw"},
        )
        languages_payload = await self._optional_json(client, f"{API}/repos/{full_name}/languages", token)
        contents = await self._optional_json(client, f"{API}/repos/{full_name}/contents/", token)
        files = []
        if isinstance(contents, list):
            files = [str(item.get("name") or "") for item in contents if isinstance(item, dict)][:40]
        languages = list(languages_payload) if isinstance(languages_payload, dict) else []
        return {
            "description": repo.get("description") or "",
            "readme": (readme or "")[:8000],
            "languages": languages,
            "files": files,
            "topics": repo.get("topics") or [],
        }

    async def _authored(self, client, token: str, full_name: str, username: str) -> bool:
        if not username:
            return False
        try:
            response = await self._request(
                client,
                "GET",
                f"{API}/repos/{quote(full_name, safe='/')}/commits",
                token,
                params={"author": username, "per_page": "1"},
            )
        except GitHubError:
            return False
        payload = response.json()
        return isinstance(payload, list) and len(payload) > 0

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
            reset = response.headers.get("x-ratelimit-reset", "")
            raise GitHubError(
                "GitHub rate limit reached.",
                f"Connect a personal access token to raise the limit, or retry after the reset time ({reset or 'later'}). Your inputs and edits are saved.",
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
    }
