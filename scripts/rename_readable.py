"""把旧计划与项目资源 Markdown 的纯哈希文件名改成「名称-短编号」，默认只预演。

用法（在 selfManger/ 目录下执行）：
    .venv\\Scripts\\python.exe scripts\\rename_readable.py --vault "<Vault 根目录>"
    .venv\\Scripts\\python.exe scripts\\rename_readable.py --vault "<Vault 根目录>" --apply

--apply 之前会先把清单与每个文件的内容哈希写入 deliverables/naming-<日期>/，
任何冲突（目标已存在、无法解析标题）都会跳过并记录，不会覆盖任何现有文件。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from selfManger.naming import is_legacy_name, readable_name, short_id  # noqa: E402


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frontmatter_value(text: str, key: str) -> str:
    if not text.startswith("---"):
        return ""
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if line.startswith(key + ":"):
            value = line[len(key) + 1:].strip().strip("'\"")
            return value
    return ""


def plan_documents(vault: Path):
    """selfManger/计划/**/*.md and selfManger/资源/*.md written with hash names."""
    changes, skipped = [], []
    for folder, key in ((vault / "selfManger/计划", "title"), (vault / "selfManger/资源", "name")):
        if not folder.is_dir():
            continue
        for path in sorted(folder.rglob("*.md")):
            if path.parent.name == "附件" or not is_legacy_name(path.stem):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            identity = frontmatter_value(text, "id") or path.stem
            title = frontmatter_value(text, key)
            if not title:
                skipped.append((str(path), "缺少标题字段，无法生成可读名称"))
                continue
            target = path.with_name(readable_name(title) + "-" + short_id(identity) + ".md")
            changes.append({"kind": "文档", "from": str(path), "to": str(target),
                            "sha256": sha256(path), "size": path.stat().st_size})
    return changes, skipped


def leftovers(vault: Path):
    """Interrupted-write temp files left by the old naming rule (report only)."""
    found = []
    for path in sorted(vault.rglob("*.tmp")):
        if ".git" in path.parts:
            continue
        if re.search(r"\.[0-9a-f]{32}\.tmp$", path.name):
            found.append({"path": str(path), "size": path.stat().st_size})
    return found


def apply_changes(changes, report_dir: Path):
    report_dir.mkdir(parents=True, exist_ok=True)
    applied, conflicts = [], []
    for change in changes:
        source, target = Path(change["from"]), Path(change["to"])
        if target.exists():
            conflicts.append({**change, "reason": "目标名称已存在"})
            continue
        source.replace(target)
        applied.append(change)
    return applied, conflicts


def main():
    parser = argparse.ArgumentParser(description="把旧计划与项目资源 Markdown 的文件名改为「名称-短编号」（默认预演）")
    parser.add_argument("--vault", required=True, help="Obsidian Vault 根目录（必须显式指定）")
    parser.add_argument("--apply", action="store_true", help="实际重命名；缺省只打印预演结果")
    parser.add_argument("--report", default="", help="清单输出目录，默认 deliverables/naming-<日期>")
    args = parser.parse_args()

    vault = Path(args.vault).expanduser().resolve()
    if not vault.is_dir():
        raise SystemExit("Vault 不存在：" + str(vault))

    changes, skipped = [], []
    changes, skipped = plan_documents(vault)

    print("Vault:", vault)
    print("待改名:", len(changes), "项；跳过:", len(skipped), "项")
    for change in changes:
        print("  改 " + Path(change["from"]).name + "  ->  " + Path(change["to"]).name)
    for path, reason in skipped:
        print("  跳过 " + path + "：" + reason)

    stale = leftovers(vault)
    if stale:
        print("发现中断残留临时文件", len(stale), "个（本次不处理，需单独确认）：")
        for item in stale:
            print("  " + item["path"])

    report_dir = Path(args.report) if args.report else Path(__file__).resolve().parents[1] / "deliverables" / (
        "naming-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    report_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"vault": str(vault), "applied": bool(args.apply), "changes": changes,
                "skipped": [{"path": p, "reason": r} for p, r in skipped], "leftovers": stale}
    (report_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    print("清单:", report_dir / "manifest.json")

    if not args.apply:
        print("\n这是预演。确认无误后加 --apply 实际执行。")
        return

    applied, conflicts = apply_changes(changes, report_dir)
    (report_dir / "applied.json").write_text(json.dumps({"applied": applied, "conflicts": conflicts},
                                                        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("已改名:", len(applied), "项；冲突跳过:", len(conflicts), "项")
    for conflict in conflicts:
        print("  冲突 " + Path(conflict["from"]).name + "：" + conflict["reason"])


if __name__ == "__main__":
    main()
