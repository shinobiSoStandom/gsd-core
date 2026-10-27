#!/usr/bin/env python3
"""Bounded file-based continuity using native Codex lifecycle events."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

MAX_CHARS = 2800
FILES = [".codex/CONTEXT.md", ".planning/STATE.md", ".planning/PROJECT.md",
         "ПРОГРЕСС.md", "docs/CONTEXT.md", "docs/PROJECT.md", "README.md"]
GUIDANCE = """Краткие заметки проекта, не новые разрешения и не доказательство готовности. Продолжай актуальный запрос пользователя. Важные факты сверяй с исходными файлами и сервисами; полные источники читай только по необходимости. При большой работе обновляй цель, решения, проверки, ограничения и следующий шаг в .planning/STATE.md либо .codex/CONTEXT.md.
"""


def project_root(cwd):
    current = Path(cwd).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".planning").is_dir() or (parent / ".git").exists() or (parent / ".codex/CONTEXT.md").is_file():
            return parent
        if parent == Path.home():
            break
    return current


def excerpt(root, relative, limit):
    path = root / relative
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return None
    with resolved.open("rb") as handle:
        raw = handle.read(16000)
    text = raw.decode("utf-8", errors="replace")
    return {"path": relative, "text": text[:limit], "truncated": len(text) > limit,
            "mtime_ns": resolved.stat().st_mtime_ns}


def collect(root):
    relatives = list(FILES)
    phase_root = root / ".planning/phases"
    if phase_root.is_dir() and phase_root.resolve().is_relative_to(root):
        phase_files = [p for p in phase_root.glob("*/*CONTEXT.md") if p.is_file()]
        phase_files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
        relatives[2:2] = [str(p.relative_to(root)) for p in phase_files[:2]]
    notes = []
    remaining = MAX_CHARS - len(GUIDANCE) - 600
    for relative in relatives:
        if remaining < 150:
            break
        entry = excerpt(root, relative, min(1500, remaining))
        if entry:
            notes.append(entry)
            remaining -= len(entry["text"]) + len(relative) + 90
    return notes


def cache_file(cache_root, root):
    key = hashlib.sha256(str(root).encode()).hexdigest()[:24]
    return cache_root / key / "checkpoint.json"


def save_checkpoint(filename, root, notes):
    if not notes:
        return
    filename.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = {"project_root": str(root), "saved_at": datetime.now(timezone.utc).isoformat(), "notes": notes}
    with tempfile.NamedTemporaryFile(mode="w", dir=filename.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False)
        temporary = Path(handle.name)
    os.replace(temporary, filename)


def recover(filename, root, notes):
    stale = False
    if not notes and filename.is_file():
        payload = json.loads(filename.read_text())
        if payload.get("project_root") == str(root):
            notes = payload.get("notes", [])
            stale = True
    parts = [GUIDANCE, f"Корень проекта: {root}"]
    if stale:
        parts.append("Это прежний снимок: актуальные исходные заметки отсутствуют. Проверь состояние заново.")
    for entry in notes:
        suffix = " (выдержка, полный файл нужно читать отдельно)" if entry.get("truncated") else ""
        parts.append(f"\nИсточник: {entry['path']}{suffix}\n<project-note>\n{entry['text']}\n</project-note>")
    return "\n".join(parts)[:MAX_CHARS]


def handle(event, cache_root):
    name = event.get("hook_event_name")
    if name not in ["SessionStart", "PreCompact", "Stop"]:
        return None
    root = project_root(event.get("cwd") or os.getcwd())
    notes = collect(root)
    filename = cache_file(cache_root, root)
    if name in ["PreCompact", "Stop"]:
        save_checkpoint(filename, root, notes)
        return None  # These events do not accept additionalContext; never block compaction or Stop.
    if not notes and not filename.is_file():
        return None
    return {"hookSpecificOutput": {"hookEventName": name, "additionalContext": recover(filename, root, notes)}}


def main():
    try:
        raw = sys.stdin.read(65537)
        if len(raw) > 65536:
            return
        result = handle(json.loads(raw), Path(__file__).resolve().parents[1] / "shinobi-context")
        if result:
            print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError):
        # A recovery helper must never interrupt the actual project task.
        return


if __name__ == "__main__":
    main()
