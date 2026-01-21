"""Sync VERSION.txt from the canonical version module."""

from __future__ import annotations

from pathlib import Path
import runpy


ROOT = Path(__file__).resolve().parents[1]
VERSION_MODULE = ROOT / "src" / "simulation_mode" / "version.py"
VERSION_TXT = ROOT / "VERSION.txt"


def load_version() -> str:
    data = runpy.run_path(str(VERSION_MODULE))
    version = data.get("__version__")
    if not isinstance(version, str) or not version:
        raise RuntimeError(f"__version__ not found in {VERSION_MODULE}")
    return version


def write_version_txt(version: str) -> None:
    VERSION_TXT.write_text(f"{version}\n", encoding="utf-8")


def main() -> None:
    version = load_version()
    write_version_txt(version)
    print(f"Synced VERSION.txt to {version}")


if __name__ == "__main__":
    main()
