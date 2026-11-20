"""Safe file storage utilities (never uses user-supplied filenames in filesystem paths)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import AsyncIterator

from backend.networking.protocol import validate_transfer_id

_UNSAFE_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


class FileTooLargeError(Exception):
    """Raised when an uploaded file exceeds the configured maximum size."""


def sanitize_display_filename(raw_name: str | None) -> str:
    """Clean a user-supplied filename for display and Content-Disposition headers only."""
    if not raw_name:
        return "unnamed.bin"
    # Strip any directory segments sent by legacy browsers
    base = raw_name.replace("\\", "/").split("/")[-1].strip()
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", base)
    if not cleaned or cleaned in (".", ".."):
        return "unnamed.bin"
    return cleaned[:255]


class StorageService:
    def __init__(self, staging_dir: Path, backups_dir: Path, max_file_size_bytes: int) -> None:
        self.staging_dir = Path(staging_dir)
        self.backups_dir = Path(backups_dir)
        self.max_file_size_bytes = max_file_size_bytes
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)

    def staging_path_for(self, transfer_id: str) -> Path:
        safe_id = validate_transfer_id(transfer_id)
        return self.staging_dir / f"{safe_id}.part"

    def backup_path_for(self, transfer_id: str) -> Path:
        safe_id = validate_transfer_id(transfer_id)
        return self.backups_dir / f"{safe_id}.blob"

    async def save_staged_stream(
        self,
        transfer_id: str,
        chunks: AsyncIterator[bytes],
    ) -> tuple[Path, int, str]:
        """
        Stream uploaded chunks to `data/staging/<transfer_id>.part` while enforcing size
        limits and computing SHA-256 in a single pass.
        """
        path = self.staging_path_for(transfer_id)
        hasher = hashlib.sha256()
        total_bytes = 0

        try:
            with path.open("wb") as out_file:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    total_bytes += len(chunk)
                    if total_bytes > self.max_file_size_bytes:
                        raise FileTooLargeError(
                            f"Uploaded file exceeds maximum allowed size of {self.max_file_size_bytes} bytes"
                        )
                    out_file.write(chunk)
                    hasher.update(chunk)
        except Exception:
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
            raise

        return path, total_bytes, hasher.hexdigest()

    def verify_backup_exists(self, transfer_id: str) -> Path | None:
        path = self.backup_path_for(transfer_id)
        return path if path.exists() and path.is_file() else None
