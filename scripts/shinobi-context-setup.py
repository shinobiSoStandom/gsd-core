#!/usr/bin/env python3
"""Register native context hooks without editing Codex's persisted trust state."""

import argparse
import json
from pathlib import Path
import shlex
import shutil
import sys


def install(config):
    source = Path(__file__).resolve().parents[1] / "personalization/codex_context.py"
    target = config / "hooks/shinobi-codex-context.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    filename = config / "hooks.json"
    document = json.loads(filename.read_text()) if filename.exists() else {"hooks": {}}
    hooks = document.setdefault("hooks", {})
    for groups in hooks.values():
        for group in groups:
            group["hooks"] = [handler for handler in group.get("hooks", [])
                              if "shinobi-codex-context.py" not in handler.get("command", "")]
    for event in list(hooks):
        hooks[event] = [group for group in hooks[event] if group.get("hooks")]
    for event, matcher in [("SessionStart", "startup|resume|clear|compact|fork"),
                           ("PreCompact", "manual|auto"), ("Stop", "*")]:
        handler = {"type": "command", "command": shlex.quote(sys.executable) + " " + shlex.quote(str(target)), "timeout": 3}
        if event == "SessionStart":
            handler["additionalContextLimit"] = 1200
        hooks.setdefault(event, []).append({"matcher": matcher, "hooks": [handler]})
    filename.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return {"script": str(target), "events": 3, "activation": "requires native /hooks review"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path.home() / ".codex")
    args = parser.parse_args()
    print(json.dumps(install(args.config_dir.expanduser().resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
