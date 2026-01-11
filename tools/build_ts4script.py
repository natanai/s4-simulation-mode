import sys
from pathlib import Path
import py_compile
import zipfile
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src" / "simulation_mode"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_ARCHIVE = DIST_DIR / "s4-simulation-mode-v0.2.ts4script"


def _require_python_37():
    if sys.version_info[:2] != (3, 7):
        version = ".".join(str(part) for part in sys.version_info[:3])
        print(
            "Error: Python 3.7.x is required to build Sims 4 script mods. "
            f"Detected {version}.",
            file=sys.stderr,
        )
        sys.exit(1)


def _iter_source_files():
    return sorted(path for path in SRC_DIR.rglob("*.py") if path.is_file())


def _compile_source(path: Path):
    relative = path.relative_to(PROJECT_ROOT / "src")
    pyc_path = BUILD_DIR / relative.with_suffix(".pyc")
    pyc_path.parent.mkdir(parents=True, exist_ok=True)
    compiled_path = py_compile.compile(
        str(path),
        cfile=str(pyc_path),
        doraise=True,
    )
    if compiled_path != str(pyc_path):
        shutil.copy2(compiled_path, pyc_path)
    return relative, pyc_path.relative_to(BUILD_DIR)


def main():
    _require_python_37()

    if not SRC_DIR.exists():
        print(f"Error: missing source directory at {SRC_DIR}.", file=sys.stderr)
        sys.exit(1)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    sources = _iter_source_files()
    if not sources:
        print(f"Error: no source files found under {SRC_DIR}.", file=sys.stderr)
        sys.exit(1)

    compiled = [
        _compile_source(source_path)
        for source_path in sources
    ]

    with zipfile.ZipFile(OUTPUT_ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source_path in sources:
            relative = source_path.relative_to(PROJECT_ROOT / "src")
            archive.write(source_path, arcname=str(relative))
        for _, pyc_relative in compiled:
            archive.write(BUILD_DIR / pyc_relative, arcname=str(pyc_relative))

    print(f"Built {OUTPUT_ARCHIVE}")


if __name__ == "__main__":
    main()
