import sys
from pathlib import Path
import py_compile
import zipfile
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_FILE = PROJECT_ROOT / "src" / "simulation_mode.py"
BUILD_DIR = PROJECT_ROOT / "build"
DIST_DIR = PROJECT_ROOT / "dist"
PYC_DEST = BUILD_DIR / "simulation_mode.pyc"
OUTPUT_ARCHIVE = DIST_DIR / "s4-simulation-mode.ts4script"


def _require_python_37():
    if sys.version_info[:2] != (3, 7):
        version = ".".join(str(part) for part in sys.version_info[:3])
        print(
            "Error: Python 3.7.x is required to build Sims 4 script mods. "
            f"Detected {version}.",
            file=sys.stderr,
        )
        sys.exit(1)


def main():
    _require_python_37()

    if not SRC_FILE.exists():
        print(f"Error: missing source file at {SRC_FILE}.", file=sys.stderr)
        sys.exit(1)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    DIST_DIR.mkdir(parents=True, exist_ok=True)

    compiled_path = py_compile.compile(
        str(SRC_FILE),
        cfile=str(PYC_DEST),
        doraise=True,
    )

    if compiled_path != str(PYC_DEST):
        shutil.copy2(compiled_path, PYC_DEST)

    with zipfile.ZipFile(OUTPUT_ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(SRC_FILE, arcname="simulation_mode.py")
        archive.write(PYC_DEST, arcname="simulation_mode.pyc")

    print(f"Built {OUTPUT_ARCHIVE}")


if __name__ == "__main__":
    main()
