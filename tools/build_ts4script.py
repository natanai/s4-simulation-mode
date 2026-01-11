from pathlib import Path
import py_compile
import shutil
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
PACKAGE_DIR = SRC_ROOT / "simulation_mode"
DIST_DIR = PROJECT_ROOT / "dist"
OUTPUT_ARCHIVE = DIST_DIR / "simulation-mode.ts4script"
BUILD_DIR = DIST_DIR / "build"
BOOTSTRAP_MODULE = SRC_ROOT / "s4_simulation_mode.py"


def _iter_source_files():
    root_files = sorted(path for path in SRC_ROOT.glob("*.py") if path.is_file())
    package_files = sorted(path for path in PACKAGE_DIR.rglob("*.py") if path.is_file())
    return root_files + package_files


def _compile_sources(sources):
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    compiled = []
    for source_path in sources:
        relative = source_path.relative_to(SRC_ROOT)
        compiled_path = BUILD_DIR / relative
        compiled_path = compiled_path.with_suffix(".pyc")
        compiled_path.parent.mkdir(parents=True, exist_ok=True)
        py_compile.compile(source_path, cfile=compiled_path, dfile=str(relative))
        compiled.append(compiled_path)
    return compiled


def main():
    if not PACKAGE_DIR.exists():
        print(f"Error: missing source directory at {PACKAGE_DIR}.", file=sys.stderr)
        sys.exit(1)

    if not BOOTSTRAP_MODULE.exists():
        print(
            f"Error: missing bootstrap module at {BOOTSTRAP_MODULE}.",
            file=sys.stderr,
        )
        sys.exit(1)

    DIST_DIR.mkdir(parents=True, exist_ok=True)

    sources = _iter_source_files()
    if not sources:
        print(f"Error: no source files found under {SRC_ROOT}.", file=sys.stderr)
        sys.exit(1)

    compiled_sources = _compile_sources(sources)

    with zipfile.ZipFile(OUTPUT_ARCHIVE, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source_path in sources:
            relative = source_path.relative_to(PROJECT_ROOT / "src")
            archive.write(source_path, arcname=str(relative))
        for compiled_path in compiled_sources:
            # Sims 4 loads .pyc bytecode from ts4script archives; removing these breaks the mod.
            relative = compiled_path.relative_to(BUILD_DIR)
            archive.write(compiled_path, arcname=str(relative))

    with zipfile.ZipFile(OUTPUT_ARCHIVE, "r") as archive:
        pyc_entries = [name for name in archive.namelist() if name.endswith(".pyc")]
        if not pyc_entries:
            print(
                f"Error: built archive {OUTPUT_ARCHIVE} contains no .pyc entries.",
                file=sys.stderr,
            )
            sys.exit(1)

    print(f"Built {OUTPUT_ARCHIVE}")
    print("Dist contents:")
    for item in sorted(DIST_DIR.iterdir()):
        print(f"- {item}")


if __name__ == "__main__":
    main()
