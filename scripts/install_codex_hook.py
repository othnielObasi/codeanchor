#!/usr/bin/env python3
"""Install or remove CodeAnchor's advisory Stop hook in Codex hooks.json."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
HOOK = (ROOT / "bin" / "codeanchor-hook").resolve()
MARKER = "codeanchor-hook"


def _handler() -> dict[str, Any]:
    return {
        "type": "command",
        "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(HOOK))}",
        "commandWindows": subprocess.list2cmdline([sys.executable, str(HOOK)]),
        "timeout": 10,
        "statusMessage": "Verifying task contract",
    }


def _is_ours(handler: Any) -> bool:
    return isinstance(handler, dict) and MARKER in str(handler.get("command", "")) + str(handler.get("commandWindows", ""))


def _transform(config: dict[str, Any], uninstall: bool) -> dict[str, Any]:
    hooks = config.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("top-level 'hooks' must be a JSON object")
    groups = hooks.setdefault("Stop", [])
    if not isinstance(groups, list):
        raise ValueError("hooks.Stop must be a JSON array")

    retained = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
            retained.append(group)
            continue
        handlers = [handler for handler in group["hooks"] if not _is_ours(handler)]
        if handlers:
            retained.append({**group, "hooks": handlers})

    if not uninstall:
        retained.append({"hooks": [_handler()]})
    if retained:
        hooks["Stop"] = retained
    else:
        hooks.pop("Stop", None)
    return config


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"refusing to overwrite invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"refusing to overwrite non-object JSON in {path}")
    return value


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="hooks-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uninstall", action="store_true", help="remove only the CodeAnchor Stop hook")
    parser.add_argument("--dry-run", action="store_true", help="print the resulting config without writing it")
    parser.add_argument("--codex-home", help=argparse.SUPPRESS)
    args = parser.parse_args()

    home = Path(args.codex_home or os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    path = home / "hooks.json"
    try:
        config = _transform(_read(path), args.uninstall)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(config, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(rendered, end="")
        return 0
    _write_atomic(path, rendered)
    action = "Removed" if args.uninstall else "Installed"
    print(f"{action} CodeAnchor Stop hook in {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
