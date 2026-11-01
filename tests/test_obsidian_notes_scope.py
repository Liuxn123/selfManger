"""Both note entry routes enforce document scope and preserve lexical paths."""
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem
from tests import test_ui_optimization as fixture
from tests.test_obsidian_document_entries import document_project
from selfManger.domain import UserError
from selfManger.obsidian import project_uri, project_notes_uri
from selfManger.project_workspaces import ProjectWorkspace
from selfManger.runtime import TaskContext


class NotesScopeTests(unittest.TestCase):
    setUpClass = classmethod(fixture.UIOptimizationTests.setUpClass.__func__)
    setUp = fixture.UIOptimizationTests.setUp
    make_window = fixture.UIOptimizationTests.make_window
    close_window = fixture.UIOptimizationTests.close_window
    until_idle = fixture.UIOptimizationTests.until_idle

    def entry(self):
        workspace = ProjectWorkspace(self.root / "workspace")
        workspace.initialize(TaskContext())
        (workspace.root / "myself/.obsidian").mkdir()
        project, item, entry = document_project(workspace)
        note = workspace.create_note(item["id"], "学习 # [说明] &", TaskContext())
        return workspace, item, entry, note

    def register_notes(self, workspace, item):
        registry = workspace.registry()
        row = next(p for p in registry["projects"] if p["id"] == item["id"])
        row["vault_subdirs"].append("笔记")
        workspace.registry_path.write_text(json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    def test_unregistered_notes_rejected_by_file_and_folder_without_writes(self):
        workspace, item, entry, note = self.entry()
        before = workspace.registry_path.read_bytes()
        original = Path(note["path"]).read_bytes()
        for operation in (lambda: project_uri(workspace,item["id"],note["relative"]),
                          lambda: project_notes_uri(workspace,item["id"],workspace.root/"myself")):
            with self.assertRaisesRegex(UserError, "笔记.*尚未接入.*agent"):
                operation()
        self.assertEqual(workspace.registry_path.read_bytes(),before)
        self.assertEqual(Path(note["path"]).read_bytes(),original)
        self.assertFalse((entry / "笔记").exists())

    def test_registered_missing_and_wrong_note_junctions_are_rejected(self):
        workspace,item,entry,note = self.entry()
        self.register_notes(workspace,item)
        for operation in (lambda: project_uri(workspace,item["id"],note["relative"]),
                          lambda: project_notes_uri(workspace,item["id"],workspace.root/"myself")):
            with self.assertRaisesRegex(UserError,"联接缺失"):
                operation()
        wrong=self.root / "wrong-notes"
        wrong.mkdir()
        workspace._link(item["vault_entry"]+"/笔记", wrong)
        for operation in (lambda: project_uri(workspace,item["id"],note["relative"]),
                          lambda: project_notes_uri(workspace,item["id"],workspace.root/"myself")):
            with self.assertRaisesRegex(UserError,"指向其他目录"):
                operation()

    def test_valid_file_and_folder_preserve_lexical_path_and_original(self):
        workspace,item,entry,note=self.entry()
        self.register_notes(workspace,item)
        workspace._link(item["vault_entry"]+"/笔记",Path(note["path"]).parent)
        before=workspace.registry_path.read_bytes()
        original=Path(note["path"]).read_bytes()
        for report,expected in ((project_uri(workspace,item["id"],note["relative"]),entry/note["relative"]),
                                (project_notes_uri(workspace,item["id"],workspace.root/"myself"),entry/"笔记")):
            self.assertEqual(parse_qs(urlparse(report["uri"]).query)["path"],[str(expected)])
            self.assertNotEqual(expected,expected.resolve())
        self.assertEqual(workspace.registry_path.read_bytes(),before)
        self.assertEqual(Path(note["path"]).read_bytes(),original)

    def test_both_ui_note_actions_open_validated_uri(self):
        window=self.make_window()
        workspace,item,entry,note=self.entry()
        self.register_notes(workspace,item)
        workspace._link(item["vault_entry"]+"/笔记",Path(note["path"]).parent)
        window.store.set_setting("obsidian_vault_root",str(workspace.root/"myself"))
        page=window.project_page
        page.notes_list.blockSignals(True)
        row=QListWidgetItem("学习说明")
        row.setData(Qt.ItemDataRole.UserRole,note["relative"])
        page.notes_list.addItem(row)
        page.notes_list.setCurrentItem(row)
        page.notes_list.blockSignals(False)
        def submit(_title,operation,callback,**kwargs):
            callback(operation(TaskContext()))
        with patch.object(page,"selected",return_value=item), patch.object(page,"workspace",return_value=workspace), \
             patch.object(page,"submit",side_effect=submit), \
             patch("selfManger.ui.projects.QDesktopServices.openUrl",return_value=True) as opened:
            page.open_selected_note()
            page.open_notes_directory()
            self.assertEqual(opened.call_count,2)
            paths=[parse_qs(urlparse(call.args[0].toString()).query)["path"][0] for call in opened.call_args_list]
            self.assertEqual(paths,[str(entry/note["relative"]),str(entry/"笔记")])
