from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from uuid import uuid4
from unittest.mock import patch

from selfManger.domain import UserError
from selfManger.project_initialization import (BUILTIN_TEMPLATES, validate_template,
                                              validate_selection, save_template, load_template, load_rules)
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext
from selfManger.project_skill_copies import prepare_skill_copies, copy_project_skills


class InitializationTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(os.environ.get("SELFMANGER_TEST_ROOT", tempfile.gettempdir())).resolve()
        self.fixture = self.base / ("selfManger-init-test-" + uuid4().hex)
        self.fixture.mkdir()
        self.workspace = ProjectWorkspace(self.fixture / "workspace")
        self.context = TaskContext()
        self.workspace.initialize(self.context)

    def tearDown(self):
        target = self.fixture.resolve()
        if target.parent != self.base or not target.name.startswith("selfManger-init-test-"):
            raise RuntimeError("Unsafe fixture cleanup target")
        shutil.rmtree(target)

    def test_creation_preserves_templates_and_copies_complete_skills(self):
        sources = self.fixture / "sources"
        skill = sources / "skill-one"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("# existing skill\n", encoding="utf-8")
        (skill / "scripts").mkdir()
        (skill / "scripts/run.py").write_text("print('never executed')\n", encoding="utf-8")
        (skill / "assets").mkdir()
        (skill / "assets/data.bin").write_bytes(bytes(range(256)))
        (skill / "empty").mkdir()
        note = sources / "knowledge.md"
        note.write_text("# original knowledge\n", encoding="utf-8")
        templates = self.workspace.root / "agent/templates"
        templates.mkdir()
        original_rules = "# 自定义基础规则\n不得执行部署。\n"
        (templates / "AGENTS.md").write_text(original_rules, encoding="utf-8")
        template = copy.deepcopy(BUILTIN_TEMPLATES[0])
        template["files"] = {"docs/开发说明.md": "# {{NAME}}\n{{GOAL}}\n{{ID}}\n"}
        selection = {"template": template, "rules": "## 开发规范\n使用现有环境。",
                     "skills": [{"name": "skill-one", "path": str(skill), "agent": "existing-agent"}],
                     "knowledge": [{"name": "knowledge", "path": str(note)}]}
        result = self.workspace.create("样例项目", "交付可核验结果", False, self.context, "sample", selection)
        project = Path(result["directory"])
        config = json.loads((project / ".selfManger/config.json").read_text(encoding="utf-8"))
        rules = (project / "AGENTS.md").read_text(encoding="utf-8")
        self.assertTrue(rules.startswith(original_rules))
        self.assertIn("使用现有环境", rules)
        self.assertIn(".selfManger/RESOURCES.md", rules)
        self.assertEqual(config["project_id"], result["project_id"])
        self.assertEqual(config["skills"][0]["loading"], "manual")
        location = config["skills"][0]["location"]
        self.assertEqual(location["base"], "project")
        copied = project / location["path"]
        self.assertEqual(location["path"], ".selfManger/skills/skill-one")
        self.assertEqual(config["skills"][0]["storage"], "copy")
        self.assertNotEqual(copied.resolve(), skill.resolve())
        self.assertEqual((copied / "SKILL.md").read_bytes(), (skill / "SKILL.md").read_bytes())
        self.assertEqual((copied / "scripts/run.py").read_bytes(), (skill / "scripts/run.py").read_bytes())
        self.assertEqual((copied / "assets/data.bin").read_bytes(), bytes(range(256)))
        self.assertTrue((copied / "empty").is_dir())
        self.assertIn(".selfManger/skills/skill-one/SKILL.md", rules)
        self.assertEqual((skill / "SKILL.md").read_text(encoding="utf-8"), "# existing skill\n")
        self.assertEqual(note.read_text(encoding="utf-8"), "# original knowledge\n")
        self.assertEqual((templates / "AGENTS.md").read_text(encoding="utf-8"), original_rules)
        self.assertIn(result["project_id"], (project / "docs/开发说明.md").read_text(encoding="utf-8"))
        self.assertFalse((project / ".selfManger/memory.md").exists())
        self.assertFalse((project / ".codex").exists())
        self.assertFalse((project / ".claude").exists())
        self.assertTrue((project / "agent/PROMPTS.md").is_file())
        handoff = (project / "agent/HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn("Skills 1 项复制到项目，知识资源 1 项保留引用", handoff)
        self.assertIn("加载效果尚未验证", handoff)

    def test_invalid_template_paths_are_rejected_before_number_reservation(self):
        bad_paths = ["../outside.md", "/absolute.md", "C:/outside.md", "agent/STATUS.md",
                     "AGENTS.md", "README.md", ".selfManger/config.json", ".git/config", ".claude/skills/x",
                     "nested/.env", "CON.txt", "trailing./file", "a//b", "a/../b", "a/./b"]
        for path in bad_paths:
            with self.subTest(path=path):
                template = dict(BUILTIN_TEMPLATES[0], files={path: "x"})
                with self.assertRaises(UserError):
                    self.workspace.create("invalid", "goal", False, self.context, "invalid", {"template": template})
                self.assertEqual(self.workspace.registry()["issued_ids"], [])
        self.assertEqual(list((self.workspace.root / "projects").iterdir()), [])

    def test_case_collisions_directory_collisions_and_unknown_schema_are_rejected(self):
        for files in ({"docs/A.md": "a", "docs/a.md": "b"}, {"docs": "a", "docs/file.md": "b"}):
            with self.assertRaises(UserError):
                validate_template(dict(BUILTIN_TEMPLATES[0], files=files))
        with self.assertRaises(UserError):
            validate_template(dict(BUILTIN_TEMPLATES[0], post_create="command"))
        with self.assertRaises(UserError):
            validate_template(dict(BUILTIN_TEMPLATES[0], schema_version=2))

    def test_missing_skill_is_rejected_without_consuming_a_project_number(self):
        selection = {"skills": [{"name": "missing", "path": str(self.fixture / "missing")} ]}
        with self.assertRaises(UserError):
            self.workspace.create("missing", "goal", False, self.context, "missing", selection)
        self.assertEqual(self.workspace.registry()["issued_ids"], [])

    def test_duplicate_resource_is_reused_and_credential_url_is_rejected(self):
        entry = {"name": "same", "path": str(self.fixture)}
        selection = validate_selection({"knowledge": [entry, entry]})
        self.assertEqual(len(selection["knowledge"]), 1)
        for url in ("file:///etc/passwd", "https://user:password@example.com", "https://[", "https://"):
            with self.subTest(url=url), self.assertRaises(UserError):
                validate_selection({"knowledge": [{"name": "url", "url": url}]})

    def test_default_creation_and_duplicate_creation_preserve_config(self):
        result = self.workspace.create("default", "goal", False, self.context, "default")
        path = Path(result["directory"]) / ".selfManger/config.json"
        original = path.read_bytes()
        with self.assertRaises(UserError):
            self.workspace.create("default", "goal", False, self.context, "default")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(len(self.workspace.registry()["issued_ids"]), 1)

    def test_only_general_template_is_builtin(self):
        self.assertEqual([t["id"] for t in BUILTIN_TEMPLATES], ["general"])

    def test_template_export_writes_valid_json_and_never_overwrites(self):
        path = self.fixture / "export.json"
        template = dict(BUILTIN_TEMPLATES[0], files={"docs/example.md": "Example"})
        self.assertEqual(save_template(path, template), path)
        self.assertEqual(load_template(path), template)
        original = path.read_bytes()
        with self.assertRaisesRegex(UserError, "已存在"):
            save_template(path, dict(BUILTIN_TEMPLATES[0], name="Changed"))
        self.assertEqual(path.read_bytes(), original)

    def test_load_rules_reads_bounded_utf8_and_validates_text_length(self):
        path = self.fixture / "rules.md"
        path.write_bytes(b"\xef\xbb\xbf## Rules\r\nKeep changes small.\r\n")
        self.assertEqual(load_rules(path), "## Rules\nKeep changes small.\n")
        path.write_bytes(b"x" * 100_001)
        with self.assertRaisesRegex(UserError, "100000 字符"):
            load_rules(path)
        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(UserError, "UTF-8"):
            load_rules(path)
        path.write_bytes(b"x" * 400_001)
        with self.assertRaisesRegex(UserError, "400 KB"):
            load_rules(path)

    def test_same_named_skills_are_rejected_without_number_reservation(self):
        entries = []
        for area in ("one", "two"):
            source = self.fixture / area / "same-name"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("# " + area, encoding="utf-8")
            entries.append({"name": area, "path": str(source)})
        with self.assertRaisesRegex(UserError, "同名"):
            self.workspace.create("collision", "goal", False, self.context, "collision", {"skills": entries})
        self.assertEqual(self.workspace.registry()["issued_ids"], [])

    def test_changed_source_and_existing_destination_are_preserved(self):
        source = self.fixture / "skill"
        source.mkdir()
        marker = source / "SKILL.md"
        marker.write_text("original", encoding="utf-8")
        plan = prepare_skill_copies([{"name": "skill", "path": str(source)}], self.context)
        project = self.workspace.root / "projects" / "fixture-project"
        project.mkdir()
        marker.write_text("user changed", encoding="utf-8")
        with self.assertRaisesRegex(UserError, "发生变化"):
            copy_project_skills(plan, self.workspace, project, self.context)
        self.assertFalse((project / ".selfManger").exists())
        plan = prepare_skill_copies([{"name": "skill", "path": str(source)}], self.context)
        destination = project / ".selfManger/skills/skill"
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("keep existing", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            copy_project_skills(plan, self.workspace, project, self.context)
        self.assertEqual((destination / "SKILL.md").read_text(encoding="utf-8"), "keep existing")
        self.assertEqual(marker.read_text(encoding="utf-8"), "user changed")

    def test_skill_links_are_rejected_before_reservation(self):
        source = self.fixture / "skill"
        source.mkdir()
        (source / "SKILL.md").write_text("skill", encoding="utf-8")
        outside = self.fixture / "outside.txt"
        outside.write_text("must not copy", encoding="utf-8")
        try:
            (source / "linked.txt").symlink_to(outside)
        except OSError:
            self.skipTest("This Windows account cannot create file symlinks")
        with self.assertRaisesRegex(UserError, "链接"):
            self.workspace.create("linked", "goal", False, self.context, "linked", {
                "skills": [{"name": "skill", "path": str(source)}]})
        self.assertEqual(self.workspace.registry()["issued_ids"], [])
        self.assertEqual(outside.read_text(encoding="utf-8"), "must not copy")

    def test_copy_failure_keeps_number_and_does_not_register_partial_project(self):
        source = self.fixture / "skill"
        source.mkdir()
        marker = source / "SKILL.md"
        marker.write_text("keep source", encoding="utf-8")
        with patch("selfManger.project_workspaces.copy_project_skills", side_effect=UserError("copy interrupted")):
            with self.assertRaisesRegex(UserError, "interrupted"):
                self.workspace.create("partial", "goal", False, self.context, "partial", {
                    "skills": [{"name": "skill", "path": str(source)}]})
        registry = self.workspace.registry()
        self.assertEqual(len(registry["issued_ids"]), 1)
        self.assertEqual(registry["projects"], [])
        self.assertEqual(len(list((self.workspace.root / "projects").iterdir())), 1)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep source")
        with self.assertRaisesRegex(UserError, "未登记项目"):
            self.workspace.create("retry", "goal", False, self.context, "retry")
        self.assertEqual(len(self.workspace.registry()["issued_ids"]), 1)


if __name__ == "__main__":
    unittest.main()
