from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    github_client_id: str
    github_client_secret: str
    oauth_callback: str

    @classmethod
    def from_env(cls) -> Settings:
        root = Path(__file__).resolve().parent.parent
        data_dir = Path(os.environ.get("IMPOSTERAPPLY_DATA_DIR", root / "data"))
        return cls(
            data_dir=data_dir,
            github_client_id=os.environ.get("GITHUB_CLIENT_ID", ""),
            github_client_secret=os.environ.get("GITHUB_CLIENT_SECRET", ""),
            oauth_callback=os.environ.get("GITHUB_OAUTH_CALLBACK_URL", ""),
        )

    @property
    def oauth_configured(self) -> bool:
        return bool(self.github_client_id and self.github_client_secret)
