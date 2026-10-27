#!/usr/bin/env python3
"""Install pinned official Core, then reapply the maintained Russian profile."""

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
PROFILE = json.loads((REPO / "personalization/profile.json").read_text())


def load_localizer():
    spec = importlib.util.spec_from_file_location("shinobi_localize", REPO / "scripts/shinobi-localize.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def snapshot(config, skills):
    destination = config / "backups" / ("shinobi-core-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
    destination.mkdir(parents=True, mode=0o700)
    candidates = [config / name for name in ["config.toml", "hooks.json", "settings.json", "settings.local.json",
                  "gsd-file-manifest.json", "get-shit-done", "gsd-core", "shinobi-localization.json"]]
    for subdir in ["agents", "hooks", "commands"]:
        candidates.extend((config / subdir).glob("gsd*"))
    candidates.extend(skills.glob("gsd-*"))
    with tarfile.open(destination / "before-install.tar.gz", "w:gz", dereference=False) as archive:
        for index, path in enumerate(candidates):
            if path.exists():
                archive.add(path.resolve(), arcname=f"{index}-{path.name}")
    return destination


def unpack_official(work):
    package = f"{PROFILE['upstream_package']}@{PROFILE['upstream_version']}"
    result = subprocess.run(["npm", "pack", package, "--ignore-scripts", "--json", "--pack-destination", str(work)],
                            check=True, capture_output=True, text=True)
    metadata = json.loads(result.stdout)[0]
    if metadata["name"] != PROFILE["upstream_package"] or metadata["version"] != PROFILE["upstream_version"]:
        raise ValueError("npm вернул другой пакет или версию")
    with tarfile.open(work / metadata["filename"]) as archive:
        archive.extractall(work / "unpacked", filter="data")
    return work / "unpacked/package", metadata


def install_one(runtime, config, allow_symlinks, package_dir):
    skills = Path.home() / ".agents/skills" if runtime == "codex" else config / "skills"
    backup = snapshot(config, skills)
    command = ["node", str(package_dir / "bin/install.js"), "--" + runtime, "--global",
               "--config-dir", str(config), "--profile=full"]
    import os
    environment = os.environ.copy()
    if allow_symlinks:
        environment["GSD_ALLOW_SYMLINKED_DEST"] = "1"
    with (backup / "install.log").open("w") as log:
        subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    retire_legacy(config, runtime)
    counts = load_localizer().apply_profile(config, skills.resolve())
    if runtime == "codex":
        subprocess.run(["python3", str(REPO / "scripts/shinobi-context-setup.py"), "--config-dir", str(config)],
                       check=True, capture_output=True, text=True)
    return {"runtime": runtime, "version": PROFILE["upstream_version"], "backup": str(backup), **counts}


def retire_legacy(config, runtime):
    legacy = config / "get-shit-done"
    if legacy.is_symlink():
        legacy.unlink()
    elif legacy.exists():
        shutil.rmtree(legacy)
    if runtime != "codex":
        return
    for name in load_localizer().catalog("agents.ru.tsv"):
        old = config / "agents" / f"{name}.md"
        if old.is_file() and old.with_suffix(".toml").is_file():
            old.unlink()
    retire_codex_hooks(config)


def retire_codex_hooks(config):
    filename = config / "hooks.json"
    if not filename.exists():
        return
    obsolete = ["gsd-prompt-guard.js", "gsd-read-guard.js", "gsd-workflow-guard.js",
                "gsd-validate-commit.sh", "gsd-context-monitor.js", "gsd-read-injection-scanner.js",
                "gsd-phase-boundary.sh", "gsd-session-state.sh"]
    document = json.loads(filename.read_text())
    for event, groups in list(document.get("hooks", {}).items()):
        kept = []
        for group in groups:
            hooks = [hook for hook in group.get("hooks", [])
                     if not any(name in hook.get("command", "") for name in obsolete)]
            if hooks:
                kept.append({**group, "hooks": hooks})
        if kept:
            document["hooks"][event] = kept
        else:
            document["hooks"].pop(event, None)
    filename.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="append", choices=["codex", "claude"])
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--allow-symlinked-dest", action="store_true")
    args = parser.parse_args()
    runtimes = args.runtime or ["codex", "claude"]
    if args.config_dir and len(runtimes) != 1:
        parser.error("--config-dir требует один --runtime")
    if "codex" in runtimes and args.config_dir and args.config_dir.expanduser().resolve() != (Path.home() / ".codex").resolve():
        parser.error("Core размещает глобальные навыки Codex в ~/.agents/skills даже с --config-dir; изолированную установку этот скрипт не выполняет")
    if shutil.which("node") is None or shutil.which("npm") is None:
        parser.error("Требуются Node.js и npm")
    defaults_file = Path.home() / ".gsd/defaults.json"
    prior_defaults = json.loads(defaults_file.read_text()) if defaults_file.exists() else {}
    results = []
    try:
        with tempfile.TemporaryDirectory(prefix="shinobi-gsd-") as directory:
            package_dir, _ = unpack_official(Path(directory))
            for runtime in runtimes:
                config = (args.config_dir or Path.home() / f".{runtime}").expanduser().resolve()
                results.append(install_one(runtime, config, args.allow_symlinked_dest, package_dir))
    finally:
        # The official installer can add a global runtime while staging another host.
        # Preserve the original preference; only Russian response language is ours.
        prior_defaults["response_language"] = PROFILE["response_language"]
        defaults_file.parent.mkdir(parents=True, exist_ok=True)
        defaults_file.write_text(json.dumps(prior_defaults, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
