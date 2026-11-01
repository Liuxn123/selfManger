"""Small, file-based project initialization plans; never execute resource scripts."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit, quote

from .domain import UserError
from .project_skill_copies import skill_destination


BASE_RULES = """## 开发规范
- 先读现有实现，复用工程结构，只改本轮范围。
- 保留已有内容与修改；未知写待核验，不编造结果。
- 按风险验证，说明改动、验证与未完成项。
"""
BUILTIN_TEMPLATES = (
    {"schema_version": 1, "id": "general", "name": "通用项目", "rules": BASE_RULES, "files": {}},
)
RESERVED = {"readme.md", "agents.md", "agent", ".selfManger"}
DEVICE_NAMES = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def _text(value, label, limit=100_000):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise UserError(f"{label}必须是文本且不超过 {limit} 字符。")
    return value


def validate_template(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise UserError("初始化模板需要 schema_version: 1 的 JSON 对象。")
    if set(value) - {"schema_version", "id", "name", "rules", "files"}:
        raise UserError("模板包含不支持的字段；只支持 schema_version/id/name/rules/files。")
    identity = _text(value.get("id"), "模板编号", 80)
    name = _text(value.get("name"), "模板名称", 120).strip()
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}", identity) or not name:
        raise UserError("模板需要非空名称和英文/数字/下划线/连字符编号。")
    rules = _text(value.get("rules", ""), "开发规范")
    files = value.get("files", {})
    if not isinstance(files, dict) or len(files) > 30:
        raise UserError("模板 files 必须是至多 30 个相对路径与文本组成的对象。")
    normalized, paths = {}, set()
    for relative, content in files.items():
        relative = _text(relative, "模板文件路径", 240).replace("\\", "/")
        parts = relative.split("/")
        if any(not p or p in {".", ".."} or re.search(r'[<>:"|?*\x00-\x1f]', p)
               or p.endswith((".", " ")) or p.split(".")[0].casefold() in DEVICE_NAMES
               or (p.startswith(".") and not (len(parts) == 1 and p == ".gitignore")) for p in parts):
            raise UserError("模板文件路径无效或指向隐藏配置目录：" + relative)
        if parts[0].casefold() in RESERVED:
            raise UserError("模板不能覆盖项目管理文件：" + relative)
        folded = relative.casefold()
        if folded in paths or any(folded.startswith(p + "/") or p.startswith(folded + "/") for p in paths):
            raise UserError("模板文件路径重复或文件/目录冲突：" + relative)
        paths.add(folded)
        normalized[relative] = _text(content, "模板文件内容")
    if len(rules) + sum(map(len, normalized.values())) > 500_000:
        raise UserError("模板规范和文件内容总计不能超过 500000 字符。")
    return {"schema_version": 1, "id": identity, "name": name, "rules": rules, "files": normalized}


def load_template(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise UserError("请选择不超过 1 MB 的 JSON 模板文件。")
    try:
        return validate_template(json.loads(path.read_text(encoding="utf-8-sig")))
    except (UnicodeError, ValueError) as exc:
        raise UserError("模板不是有效的 UTF-8 JSON 文件。") from exc


def load_rules(path: Path) -> str:
    """Read and validate a bounded UTF-8 development-rules file."""
    path = Path(path).expanduser()
    if not path.is_file() or path.stat().st_size > 400_000:
        raise UserError("规范文件需要是最多 100000 字符的 UTF-8 文本（文件不超过 400 KB）。")
    with path.open("rb") as source:
        data = source.read(400_001)
    if len(data) > 400_000:
        raise UserError("规范文件需要是最多 100000 字符的 UTF-8 文本（文件不超过 400 KB）。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as exc:
        raise UserError("规范文件不是有效的 UTF-8 文本。") from exc
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    validate_selection({"rules": text})
    return text


def save_template(path: Path, value: dict) -> Path:
    """Export one validated template without replacing an existing file."""
    template = validate_template(value)
    target = Path(path).expanduser()
    try:
        with target.open("x", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(template, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError as exc:
        raise UserError("目标模板文件已存在；请选择新文件名。") from exc
    return target


def validate_selection(value: dict | None, *, check_sources=False) -> dict:
    if value is None:
        value = {"template": copy.deepcopy(BUILTIN_TEMPLATES[0])}
    if not isinstance(value, dict) or set(value) - {"template", "rules", "skills", "knowledge"}:
        raise UserError("项目初始化选择格式无效。")
    template = validate_template(value.get("template", BUILTIN_TEMPLATES[0]))
    rules = _text(value.get("rules", template["rules"]), "开发规范")
    if len(rules) + sum(map(len, template["files"].values())) > 500_000:
        raise UserError("选定规范和模板文件内容总计不能超过 500000 字符。")
    result = {"template": template, "rules": rules, "skills": [], "knowledge": []}
    for kind in ("skills", "knowledge"):
        entries = value.get(kind, [])
        if not isinstance(entries, list) or len(entries) > 100:
            raise UserError("每类最多选择 100 个资源。")
        seen = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise UserError("初始化资源必须是对象。")
            item = {key: _text(entry.get(key, ""), "资源字段", 2000)
                    for key in ("id", "name", "path", "url", "agent")}
            if not item["name"].strip():
                raise UserError("资源名称不能为空。")
            if kind == "skills" and not item["path"]:
                raise UserError("Skill 需要已有目录路径。")
            if item["path"]:
                source = Path(item["path"]).expanduser()
                if not source.is_absolute():
                    raise UserError("资源来源必须是绝对路径。")
                item["path"] = str(source)
                if check_sources:
                    if kind == "skills" and (not source.is_dir() or not (source / "SKILL.md").is_file()):
                        raise UserError("已有 Skill 不可用或缺少 SKILL.md：" + item["name"])
                    if kind == "knowledge" and not source.exists():
                        raise UserError("知识资源位置已失效，请重新选择：" + item["name"])
            if item["url"]:
                try:
                    parsed = urlsplit(item["url"])
                    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                        raise ValueError("invalid resource URL")
                except ValueError as exc:
                    raise UserError("知识链接需要不含账户口令的有效 HTTP(S) 地址。") from exc
            if not item["path"] and not item["url"]:
                raise UserError("资源需要本地路径或网页地址。")
            key = os.path.normcase(item["path"]) if item["path"] else item["url"]
            if key not in seen:
                seen.add(key)
                result[kind].append(item)
    destinations = [skill_destination(item).casefold() for item in result["skills"]]
    if len(set(destinations)) != len(destinations):
        raise UserError("选中的 Skills 有同名目录，请仅选择一个版本后创建。")
    return result


def _location(source: str, directory: Path) -> dict:
    if not source:
        return {}
    try:
        return {"base": "project", "path": Path(os.path.relpath(source, directory)).as_posix()}
    except ValueError:  # Different Windows drives cannot have a relative path.
        return {"base": "absolute", "path": source}


def initialization_files(selection: dict, directory: Path, replacements: dict[str, str]) -> tuple[str, dict[str, str]]:
    """Return additional AGENTS text and exact new files, without filesystem mutation."""
    def render(text):
        size = len(text)
        for key, value in replacements.items():
            token = "{{" + key + "}}"
            size += text.count(token) * (len(value) - len(token))
        if size > 500_000:
            raise UserError("模板占位符展开后单个文件不能超过 500000 字符。")
        return re.sub(r"\{\{(NAME|ID|GOAL|DATE)\}\}", lambda m: replacements[m[1]], text)

    config = {"schema_version": 1, "project_id": replacements["ID"],
              "template": {"id": selection["template"]["id"], "name": selection["template"]["name"],
                           "files": list(selection["template"]["files"])},
              "rules": {"path": "AGENTS.md"}, "skills": [], "knowledge": []}
    lines = ["# 项目初始化资源", "", "选中的 Skills 完整复制到项目 .selfManger/skills；知识资料保留引用。复制不会修改原有技能或全局 Agent 配置。",
             "支持 AGENTS.md 的 Code Agent 可从项目规则进入；其他 Agent 请手动指定文件或使用已有规则配置。",
             "项目技能副本可随项目迁移；知识引用及技能来源记录迁移后请核对。", "", "## 项目 Skills 副本", ""]
    for kind, heading in (("skills", ""), ("knowledge", "## 知识库资源")):
        if heading:
            lines.extend(["", heading, ""])
        for entry in selection[kind]:
            record = {key: entry[key] for key in ("id", "name", "url", "agent") if entry[key]}
            record["location"] = _location(entry["path"], directory)
            if kind == "skills":
                record["source"] = record["location"]
                record["location"] = {"base": "project", "path": skill_destination(entry)}
                record["storage"] = "copy"
                record["loading"] = "manual"
            config[kind].append(record)
            location = record["location"].get("path", "") or entry["url"]
            lines.append(f"- {entry['name']}：`{location}`" + ("；完整技能副本（含脚本和资源），按目标 Agent 规则确认 SKILL.md 加载，不自动执行脚本。" if kind == "skills" else ""))
        if not selection[kind]:
            lines.append("未选择。")
    files = {path: render(text) for path, text in selection["template"]["files"].items()}
    files[".selfManger/config.json"] = json.dumps(config, ensure_ascii=False, indent=2) + "\n"
    files[".selfManger/RESOURCES.md"] = "\n".join(lines) + "\n"
    rules = "\n\n" + render(selection["rules"]).strip() + "\n\n## selfManger项目配置\n\n"
    rules += "- 初始化选择见 [.selfManger/config.json](.selfManger/config.json)。\n"
    rules += "- 已选 Skills 与知识入口见 [.selfManger/RESOURCES.md](.selfManger/RESOURCES.md)，仅在本轮相关且已获授权时按需读取。配置记录不代表 Agent 已自动加载。\n"
    if selection["skills"]:
        rules += "\n### 项目内 Skills\n\n以下为项目内完整副本，按本轮任务需要读取对应 SKILL.md；不能仅凭复制就认定 Agent 已自动加载。\n"
        for entry in selection["skills"]:
            rules += f"- {entry['name']}：[阅读技能说明]({quote(skill_destination(entry) + '/SKILL.md')})。\n"
    return rules, files
