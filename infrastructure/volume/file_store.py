"""
Unity Catalog Volume I/O — upload protocols, download exports.

All reads/writes go through /Volumes/{catalog}/main/{sub_volume}/.
The Spark session has read/write access to these volumes via the
Databricks Apps service principal (SSO, no credentials needed here).
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

_CATALOG = os.getenv("ADS_CATALOG", "ads_automation")


def _volume_path(sub_volume: str, filename: str) -> str:
    """Return the DBFS-style path for a Unity Catalog Volume file."""
    return f"/Volumes/{_CATALOG}/main/{sub_volume}/{filename}"


class VolumeFileStore:
    """
    Thin wrapper around Python file I/O against Unity Catalog Volumes.

    Databricks mounts UC Volumes at /Volumes/... in the container FS,
    so plain open() / Path.write_bytes() works without any special SDK calls.
    """

    PROTOCOLS_VOLUME = "protocols"
    EXPORTS_VOLUME = "exports"
    DATA_DICT_VOLUME = "data_dictionary"

    def __init__(self) -> None:
        pass

    # ── Protocol upload ────────────────────────────────────────────────────────

    def upload_protocol(self, filename: str, content: bytes) -> str:
        """
        Write uploaded protocol bytes to the protocols volume.
        Returns the volume path where the file was written.
        """
        path = _volume_path(self.PROTOCOLS_VOLUME, filename)
        _ensure_parent(path)
        Path(path).write_bytes(content)
        logger.info("Protocol uploaded: %s (%d bytes)", path, len(content))
        return path

    def read_protocol(self, filename: str) -> bytes:
        """Read a protocol file from the protocols volume."""
        path = _volume_path(self.PROTOCOLS_VOLUME, filename)
        return Path(path).read_bytes()

    def list_protocols(self) -> list[str]:
        """List all files in the protocols volume."""
        volume_dir = Path(f"/Volumes/{_CATALOG}/main/{self.PROTOCOLS_VOLUME}")
        if not volume_dir.exists():
            return []
        return sorted(
            f.name for f in volume_dir.iterdir()
            if f.is_file() and not f.name.startswith(".")
        )

    # ── Export download ────────────────────────────────────────────────────────

    def write_export(self, session_id: str, filename: str, content: bytes) -> str:
        """
        Write an export file (Excel/CSV/DOCX) to the exports volume.
        Returns the volume path.
        """
        safe_name = f"{session_id[:8]}_{filename}"
        path = _volume_path(self.EXPORTS_VOLUME, safe_name)
        _ensure_parent(path)
        Path(path).write_bytes(content)
        logger.info("Export written: %s (%d bytes)", path, len(content))
        return path

    def read_export(self, session_id: str, filename: str) -> bytes:
        safe_name = f"{session_id[:8]}_{filename}"
        path = _volume_path(self.EXPORTS_VOLUME, safe_name)
        return Path(path).read_bytes()

    # ── Data dictionary ────────────────────────────────────────────────────────

    def upload_data_dictionary(self, filename: str, content: bytes) -> str:
        """Store the PHD data dictionary Excel for metadata ingestion."""
        path = _volume_path(self.DATA_DICT_VOLUME, filename)
        _ensure_parent(path)
        Path(path).write_bytes(content)
        logger.info("Data dictionary stored: %s", path)
        return path

    def read_data_dictionary(self, filename: str) -> bytes:
        path = _volume_path(self.DATA_DICT_VOLUME, filename)
        return Path(path).read_bytes()

    def list_data_dictionaries(self) -> list[str]:
        volume_dir = Path(f"/Volumes/{_CATALOG}/main/{self.DATA_DICT_VOLUME}")
        if not volume_dir.exists():
            return []
        return sorted(
            f.name for f in volume_dir.iterdir()
            if f.is_file() and f.suffix.lower() in (".xlsx", ".xls")
        )


def _ensure_parent(path: str) -> None:
    """Create the parent directory if it doesn't exist."""
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
