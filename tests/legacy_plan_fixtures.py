"""Write deterministic old PlanBook files without using application write APIs."""
from pathlib import Path

import yaml


FOLDERS = {"plan": "安排", "goal": "目标", "recurring": "周期任务",
           "template": "模板", "review": "复盘"}


def write_legacy_plan(root, kind, identity, metadata, body, filename=None):
    folder = FOLDERS[kind]
    path = Path(root) / "计划" / folder / (filename or (identity + ".md"))
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "---\n" + yaml.safe_dump({"id": identity, "type": kind, **metadata},
                                     allow_unicode=True, sort_keys=False) + "---\n\n" + body
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
