# ImposterApply

Web app that builds a role-specific resume from an uploaded resume PDF and GitHub repositories the user permits.

Enter a GitHub user ID to scan that account's public repositories and build a project map from each README and source files. Paste a job description to get the top three matches, or choose another mapped project. Match reasons stay on screen and are not written into the resume. LinkedIn import, OCR, and job submission are out of scope.

## Run locally

```bash
uv sync --group dev
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

```bash
uv run pytest
```

## Environment

Names only. Do not commit values.

- `GITHUB_CLIENT_ID` and `GITHUB_CLIENT_SECRET` enable GitHub OAuth. Without them, use a public username or a personal access token.
- `GITHUB_OAUTH_CALLBACK_URL` must match the GitHub OAuth app callback. Default is this app's `/api/github/oauth/callback`.
- `IMPOSTERAPPLY_DATA_DIR` stores session state. Default is `./data`.

Private repository contents are not written into the PDF unless the user opts in on the draft.
