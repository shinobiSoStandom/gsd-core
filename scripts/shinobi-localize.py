#!/usr/bin/env python3
"""Apply the Russian Shinobi profile to an installed, unchanged GSD engine."""

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import tomllib

SOURCE = Path(__file__).resolve().parents[1] / "personalization"
START = "<!-- shinobi-profile:start -->"
END = "<!-- shinobi-profile:end -->"


def catalog(filename):
    with (SOURCE / filename).open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    result = {row["id"]: row for row in rows}
    if len(result) != len(rows):
        raise ValueError(f"Повтор идентификатора в {filename}")
    return result


def replace_field(text, field, value):
    pattern = rf"(?m)^{re.escape(field)} = .*?$"
    replacement = f"{field} = {json.dumps(value, ensure_ascii=False)}"
    updated, count = re.subn(pattern, lambda _: replacement, text, count=1)
    if count != 1:
        raise ValueError(f"Не найдено поле TOML: {field}")
    return updated


def translate_frontmatter(text, row):
    match = re.match(r"\A---\n(.*?)\n---(?:\n|$)", text, re.S)
    if not match:
        raise ValueError(f"Нет frontmatter: {row['id']}")
    header = re.sub(r"(?ms)^description:.*?(?=\n\S|\Z)",
                    "description: " + json.dumps(row["description"], ensure_ascii=False),
                    match[1], count=1)
    header = re.sub(r"(?m)^(  short-description:) .*?$",
                    lambda m: m[1] + " " + json.dumps("GSD · " + row["title"], ensure_ascii=False), header)
    # Keep the technical skill name and all runtime-specific frontmatter intact.
    return "---\n" + header + "\n---\n" + text[match.end():]


def profile_block(title, instructions):
    return f"{START}\n## GSD · {title}\n\n{instructions.strip()}\n{END}\n"


def remove_profile(text):
    return re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", "", text, flags=re.S)


def localize_markdown(text, row, instructions):
    text = translate_frontmatter(remove_profile(text), row)
    marker = text.index("\n---\n", 4) + len("\n---\n")
    return text[:marker] + profile_block(row["title"], instructions) + text[marker:]


def localize_toml(text, row, instructions):
    parsed = tomllib.loads(text)
    if parsed.get("name") != row["id"]:
        raise ValueError(f"Несовпадение роли TOML: {row['id']}")
    updated = replace_field(text, "description", row["description"])
    original = parsed["developer_instructions"]
    translated = profile_block(row["title"], instructions) + remove_profile(original)
    match = re.search(r"(?m)^developer_instructions\s*=\s*('''|\"\"\")", updated)
    if not match:
        raise ValueError("Неизвестный формат developer_instructions")
    end = updated.index(match[1], match.end())
    updated = updated[:match.end()] + translated + updated[end:]
    validated = tomllib.loads(updated)
    assert validated["name"] == parsed["name"]
    assert validated["developer_instructions"] == translated
    return updated


def build_changes(config_dir, skills_dir):
    skills = catalog("skills.ru.tsv")
    agents = catalog("agents.ru.tsv")
    instructions = (SOURCE / "instructions.ru.md").read_text()
    directive = ("Отвечать и писать новые документы по-русски; сохранять технические имена и форматы GSD. "
                 "Не выдавать локальную проверку за публикацию или доставку. "
                 "Подробные правила проекта читать только для соответствующей задачи, если ещё не загружены: "
                 + str(config_dir / "gsd-core/references/shinobi-profile-ru.md")
                 + ". Передавать помощникам только конкретное задание и нужные файлы; не перечитывать всю историю.")
    changes = {}
    counts = {"skills": 0, "agent_files": 0}
    for path in sorted(skills_dir.glob("gsd-*/SKILL.md")):
        key = path.parent.name
        if key == "gsd-dev-preferences":
            continue  # This skill belongs to the user, not the distribution.
        if key not in skills:
            raise ValueError(f"Новая команда требует перевода: {key}")
        changes[path] = localize_markdown(path.read_text(), skills[key], directive)
        counts["skills"] += 1
    for root in [config_dir / "agents", config_dir / "gsd-core/agents"]:
        for path in sorted(root.glob("gsd-*")):
            if path.suffix not in [".md", ".toml"]:
                continue
            key = path.name.removesuffix(".md").removesuffix(".toml").removesuffix(".compact")
            if key not in agents:
                raise ValueError(f"Новая роль требует перевода: {key}")
            convert = localize_toml if path.suffix == ".toml" else localize_markdown
            changes[path] = convert(path.read_text(), agents[key], directive)
            counts["agent_files"] += 1
    if not counts["skills"] or not counts["agent_files"]:
        raise ValueError("Установка не содержит ожидаемых навыков или агентов")
    reference = config_dir / "gsd-core/references/shinobi-profile-ru.md"
    changes[reference] = instructions
    changes[config_dir / "gsd-core/РУКОВОДСТВО.md"] = russian_guide(skills)
    return changes, counts


def russian_guide(skills):
    lines = ["# GSD Core — русское руководство Shinobi", "",
             "Начать существующий проект: gsd-onboard. Найти следующий шаг: gsd-next.",
             "Полный цикл: gsd-discuss-phase → gsd-plan-phase → gsd-execute-phase → gsd-verify-work → gsd-ship.",
             "В Codex команды можно упоминать как $gsd-help; в Claude Code — /gsd-help.",
             "Русифицированы описания и ответы. Технические имена и подробные оригинальные workflows сохранены.", "",
             "| Команда | Название | Когда применять |", "|---|---|---|"]
    lines.extend(f"| `{key}` | {row['title']} | {row['description']} |" for key, row in skills.items())
    return "\n".join(lines) + "\n"


def apply_profile(config_dir, skills_dir):
    # Validate the entire plan before the first write: unknown roles never cause half a translation.
    changes, counts = build_changes(config_dir, skills_dir)
    hashes = {}
    for path, content in changes.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_text() != content:
            path.write_text(content)
        hashes[str(path)] = hashlib.sha256(content.encode()).hexdigest()
    record = {"profile": json.loads((SOURCE / "profile.json").read_text()),
              "counts": counts, "files": hashes}
    (config_dir / "shinobi-localization.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=["codex", "claude"], required=True)
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--skills-dir", type=Path)
    args = parser.parse_args()
    config = (args.config_dir or Path.home() / f".{args.runtime}").expanduser().resolve()
    skills = args.skills_dir or (Path.home() / ".agents/skills" if args.runtime == "codex" else config / "skills")
    result = apply_profile(config, skills.expanduser().resolve())
    print(json.dumps({"runtime": args.runtime, **result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
