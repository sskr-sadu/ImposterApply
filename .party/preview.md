# Local Preview

Verified in this repository. No database or other service is required.

## Dependency setup

From the repository root:

```bash
uv sync --group dev
```

This creates `.venv` and installs the app plus pytest.

## Required services

None. Session state is a local SQLite file under `data/` (or `IMPOSTERAPPLY_DATA_DIR`).

## Environment prerequisites

Optional names only:

- `GITHUB_CLIENT_ID`
- `GITHUB_CLIENT_SECRET`
- `GITHUB_OAUTH_CALLBACK_URL`
- `IMPOSTERAPPLY_DATA_DIR`

Public GitHub usernames and pasted personal access tokens work without those variables. Do not record secret values here.

## Startup commands

Bind loopback only. Replace the port with the one Party allocated:

```bash
./scripts/dev.sh 8000
```

Equivalent:

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Run from the repository root so `app` imports and `./data` resolve.

## Host, port, and networking

- Host: `127.0.0.1` only. Do not bind `0.0.0.0`.
- Port: whatever `party port allocate` reserved for the session.
- No HMR. Static files are served by the same process.
- Preview command, from any working directory:

```bash
bash scripts/dev.sh <port>
```

The script changes to the repository root, then runs `uv run uvicorn app.main:app --host 127.0.0.1 --port <port>`.

## Verification

`GET /api/health` returns `{"ok": true}`. `GET /` returns the resume workflow HTML.

## Quirks and troubleshooting

- `uv run` needs the project virtualenv from `uv sync --group dev` first.
- GitHub OAuth redirects only work when `GITHUB_OAUTH_CALLBACK_URL` matches the GitHub app and the Preview origin. Otherwise use a username or personal access token.
- Restarting the process keeps sessions if `data/sessions.sqlite` is still present.
