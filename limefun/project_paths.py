"""Locate project sources for both Python and packaged desktop entry points."""
from pathlib import Path
import os
import sys


def project_root():
    override = os.environ.get("LIMEFUN_ROOT")
    if override:
        root = Path(override).resolve()
    elif getattr(sys, "frozen", False):
        for parent in Path(sys.executable).resolve().parents:
            if (parent / "limefun" / "player_server.py").is_file():
                root = parent
                break
            marker = parent / "project-root.txt"
            if marker.is_file():
                root = Path(marker.read_text(encoding="utf-8").strip()).resolve()
                break
        else:
            raise RuntimeError("Missing project-root.txt beside the LimeFun runtime")
    else:
        root = Path(__file__).resolve().parent.parent
    if not (root / "limefun" / "player_server.py").is_file():
        raise RuntimeError(f"Invalid LimeFun project root: {root}")
    return root


PROJECT_ROOT = project_root()
