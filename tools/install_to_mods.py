import os
import sys
from pathlib import Path
import shutil

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIST_FILE = PROJECT_ROOT / "dist" / "s4-simulation-mode-v0.2.ts4script"
SUBFOLDER_NAME = "SimulationMode"


def _default_mods_dirs():
    home = Path.home()
    base = home / "Documents" / "Electronic Arts" / "The Sims 4" / "Mods"
    return [base]


def _resolve_mods_dir():
    env_dir = os.environ.get("SIMS4_MODS_DIR")
    if env_dir:
        return Path(env_dir).expanduser()
    return _default_mods_dirs()[0]


def main():
    if not DIST_FILE.exists():
        print(
            f"Error: {DIST_FILE} not found. Run tools/build_ts4script.py first.",
            file=sys.stderr,
        )
        sys.exit(1)

    mods_dir = _resolve_mods_dir()
    target_dir = mods_dir / SUBFOLDER_NAME
    target_dir.mkdir(parents=True, exist_ok=True)

    destination = target_dir / DIST_FILE.name
    shutil.copy2(DIST_FILE, destination)

    print(f"Installed to {destination}")


if __name__ == "__main__":
    main()
