"""Check tracked files for accidental credentials and local runtime state."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from app.config import ROOT


KEY_PATTERN = re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")


def audit(root: Path) -> dict:
    tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
    tracked = [Path(name) for name in tracked if name]
    forbidden = []
    secrets = []
    for relative in tracked:
        if relative.name == ".env" or any(part in {".local", ".venv", "__pycache__"} for part in relative.parts):
            forbidden.append(relative.as_posix())
        path = root / relative
        if path.suffix.lower() not in {".py", ".js", ".html", ".css", ".md", ".json", ".txt", ".example"}:
            continue
        if KEY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
            secrets.append(relative.as_posix())
    return {"tracked_files": len(tracked), "forbidden_paths": forbidden, "possible_api_keys": secrets}


def main() -> None:
    report = audit(ROOT)
    print(json.dumps(report, ensure_ascii=False))
    if report["forbidden_paths"] or report["possible_api_keys"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
