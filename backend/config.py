"""Central configuration for CodeAtlas. All values can be overridden with CODEATLAS_* env vars."""
from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _default_temp_dir() -> Path:
    """Session storage outside the project directory.

    The project may live in a cloud-synced folder (OneDrive/Dropbox); cloned
    repositories placed there would be uploaded by the sync client, undermining
    the local-only privacy model and causing file-lock churn. The system temp
    directory is local and not synced. Override with CODEATLAS_TEMP_DIR.
    """
    return Path(tempfile.gettempdir()) / "codeatlas"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEATLAS_", env_file=".env", extra="ignore")

    # Session / storage
    temp_dir: Path = Field(default_factory=_default_temp_dir)

    # Cloning
    clone_depth: int = 1
    max_repo_size_mb: int = 0  # 0 = no limit; set a positive value to cap clone size
    # Abort clones that stall below this transfer rate for this long (handles hung networks).
    clone_low_speed_limit_bytes: int = 1000
    clone_low_speed_time_seconds: int = 30

    # Scanning
    max_file_size_bytes: int = 1_000_000  # skip reading content of files larger than this
    max_files: int = 10_000  # hard cap on files included in a scan

    # Chunking (bge-small handles ~512 tokens; ~2000 chars keeps chunks within that)
    chunk_max_chars: int = 2000
    chunk_overlap_lines: int = 5  # context carried between parts of a split chunk

    # Embeddings / retrieval
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_batch_size: int = 32
    top_k: int = 8  # default number of chunks returned by retrieval

    # API
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    def session_dir(self, session_id: str) -> Path:
        return self.temp_dir / f"session_{session_id}"


settings = Settings()
