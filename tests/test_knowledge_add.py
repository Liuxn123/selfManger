"""Add routing and persistence with an isolated library; no real user data."""
from pathlib import Path
import json
from unittest.mock import Mock, patch
import unittest

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLineEdit, QTabWidget
from tests import test_knowledge as fixture


class KnowledgeAddTests(unittest.TestCase):
    setUpClass = classmethod(fixture.KnowledgeBrowserTests.setUpClass.__func__)
    setUp = fixture.KnowledgeBrowserTests.setUp

    def inspect_dialog(self, inspect):
        def execute(dialog):
            inspect(dialog)
            return QDialog.DialogCode.Rejected
        return patch.object(QDialog, "exec", execute)

    def test_add_and_empty_actions_share_topic_default(self):
        page = self.page
        page.current_folder, page.directory_mode = "02-知识/Skills", True
        with patch.object(page, "collect_resource") as collect:
            page.create_button.click()
            page.add_to_current_topic()
        self.assertEqual(collect.call_count, 2)
        collect.assert_called_with("file", default_folder="02-知识/Skills")
        self.assertEqual([a.text() for a in page.manage_button.menu().actions()],
                         ["刷新索引", "迁入旧工作台资源…", "打开当前专题目录", "打开知识库目录"])

    def test_general_view_defaults_to_inbox_and_cancel_writes_nothing(self):
        page = self.page
        page.current_folder, page.directory_mode = "02-知识/Skills", False
        before = sorted(str(p) for p in self.vault.rglob("*"))
        submissions = self.window.submissions
        def inspect(dialog):
            self.assertEqual(dialog.findChild(QComboBox, "KnowledgeDestination").currentData(),
                             "02-知识/00-收件箱")
            self.assertEqual(dialog.findChild(QTabWidget).currentIndex(), 1)
        with self.inspect_dialog(inspect):
            page.add_resource()
        self.assertEqual(before, sorted(str(p) for p in self.vault.rglob("*")))
        self.assertEqual(self.window.submissions, submissions)

    def test_deep_topic_is_available_and_unique(self):
        page = self.page
        target = "02-知识/a/b/c/d/e"
        page.current_folder, page.directory_mode = target, True
        page.folders += [target, "02-知识/00-收件箱"]
        def inspect(dialog):
            destination = dialog.findChild(QComboBox, "KnowledgeDestination")
            self.assertEqual(destination.currentData(), target)
            self.assertEqual(sum(destination.itemData(i) == "02-知识/00-收件箱"
                                 for i in range(destination.count())), 1)
        with self.inspect_dialog(inspect):
            page.add_resource()

    def save_file(self, copy):
        page = self.page
        source = self.vault.parent / "original.txt"
        source.write_text("unchanged source", encoding="utf-8")
        target = "02-知识/Skills"
        page.current_folder, page.directory_mode = target, True
        self.window.statusBar = Mock(return_value=Mock())
        def inspect(dialog):
            mode = next(c for c in dialog.findChildren(QComboBox)
                        if c.count() == 2 and c.itemText(0).startswith("复制到"))
            self.assertTrue(mode.currentData())
            mode.setCurrentIndex(mode.findData(copy))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        with self.inspect_dialog(inspect), patch.object(page, "refresh"):
            page.collect_resource("file", sources=[str(source)])
        self.assertEqual(source.read_text(encoding="utf-8"), "unchanged source")
        items = page.index.scan()["items"]
        added = next(item for item in items if item["folder"] == target and item["kind"] != "folder")
        self.assertEqual(added["relative"].startswith("external:"), not copy)
        if copy:
            self.assertEqual((self.vault / target / source.name).read_bytes(), source.read_bytes())
        else:
            self.assertEqual(Path(added["path"]), source)

    def test_default_file_copy_preserves_source(self):
        self.save_file(True)

    def test_file_copy_reports_partial_destination_if_cleanup_fails(self):
        source = self.vault.parent / "original.txt"
        source.write_text("source", encoding="utf-8")
        target = self.vault / "docs" / source.name
        original_unlink = Path.unlink

        def fail_target_cleanup(path, *args, **kwargs):
            if path == target:
                raise PermissionError("synthetic locked copy")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_target_cleanup), \
                patch("selfManger.knowledge.shutil.copystat", side_effect=OSError("metadata copy failed")):
            with self.assertRaises(fixture.UserError) as raised:
                self.page.index.add_file(source, "docs", copy=True)
        self.assertIn(str(target), str(raised.exception))
        self.assertIn("PermissionError", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertTrue(target.is_file())

    def test_reference_saved_in_topic_preserves_source(self):
        self.save_file(False)

    def test_failed_batch_folder_cleanup_is_reported(self):
        page = self.page
        sources = [self.vault.parent / "first.txt", self.vault.parent / "second.txt"]
        for source in sources:
            source.write_text("source", encoding="utf-8")
        page.current_folder, page.directory_mode = "02-知识/Skills", True
        self.window.statusBar = Mock(return_value=Mock())
        def inspect(dialog):
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        with patch.object(page.index, "add_file", side_effect=OSError("copy failed")), \
                patch.object(page.index, "delete_empty_folder", side_effect=OSError("folder locked")), \
                patch("selfManger.ui.knowledge.QMessageBox.information") as information, \
                patch.object(page, "refresh"), self.inspect_dialog(inspect):
            page.collect_resource("file", sources=[str(source) for source in sources])
        self.assertIn("空收集文件夹清理失败：folder locked", information.call_args.args[2])

    def test_failed_group_move_reports_folder_cleanup_error(self):
        from selfManger.ui.knowledge import QInputDialog

        page = self.page
        page.current_folder, page.directory_mode = "02-知识/Skills", True
        page.refresh = Mock()
        page.index.create_folder = Mock()
        page.index.move_file = Mock(side_effect=OSError("move failed"))
        page.index.delete_empty_folder = Mock(side_effect=OSError("folder locked"))
        row = next(i for i, entry in enumerate(page.shown) if entry["relative"] == "docs/code.py")
        page.file_list.setCurrentCell(row, 0)
        page.file_list.selectRow(row)
        page.cards.setCurrentRow(row)
        self.app.processEvents()
        with patch.object(QInputDialog, "getText", return_value=("Grouped", True)), \
                patch("selfManger.ui.knowledge.QMessageBox.information") as information:
            page.group_selected()
        self.assertIn("空文件夹清理失败，请检查：folder locked", information.call_args.args[2])

    def test_change_destination_and_switch_to_link_before_save(self):
        page = self.page
        page.current_folder, page.directory_mode = "02-知识/Skills", True
        self.window.statusBar = Mock(return_value=Mock())
        def inspect(dialog):
            tabs = dialog.findChild(QTabWidget)
            tabs.setCurrentIndex(0)
            url = next(w for w in dialog.findChildren(QLineEdit)
                       if w.placeholderText().startswith("粘贴网页"))
            url.setText("https://example.com/resource")
            destination = dialog.findChild(QComboBox, "KnowledgeDestination")
            destination.setCurrentIndex(destination.findData("02-知识/00-收件箱"))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        with self.inspect_dialog(inspect), patch.object(page, "refresh"):
            page.add_resource()
        added = next(item for item in page.index.scan()["items"]
                     if item["folder"] == "02-知识/00-收件箱" and item["kind"] == "link")
        self.assertIn("https://example.com/resource", Path(added["path"]).read_text(encoding="utf-8-sig"))
        self.assertEqual(page.quick_filter, "inbox")

    def directory_source(self):
        source = self.vault.parent / "package"
        (source / "nested" / "empty").mkdir(parents=True)
        (source / "SKILL.md").write_text("# Test skill", encoding="utf-8")
        (source / "nested" / "data.bin").write_bytes(bytes(range(256)))
        return source

    def test_directory_dialog_copies_tree_and_registers_local_bundle(self):
        source = self.directory_source()
        page = self.page
        page.current_folder, page.directory_mode = "02-知识/Skills", True
        self.window.statusBar = Mock(return_value=Mock())
        def inspect(dialog):
            self.assertEqual(dialog.findChild(QTabWidget).currentIndex(), 3)
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        with self.inspect_dialog(inspect), patch.object(page, "refresh"):
            page.collect_resource("directory", prefill=str(source))
        target = self.vault / "02-知识/Skills/package"
        self.assertEqual((target / "nested/data.bin").read_bytes(), (source / "nested/data.bin").read_bytes())
        self.assertTrue((target / "nested/empty").is_dir())
        bundle = next(r for r in page.index.read_bundles([]) if r["directory"] == "02-知识/Skills/package")
        self.assertEqual(bundle["folder"], "02-知识/Skills")
        (source / "nested/data.bin").write_bytes(b"source changed")
        self.assertEqual((target / "nested/data.bin").read_bytes(), bytes(range(256)))

    def test_legacy_bundle_json_imports_once_and_new_bundles_are_sqlite_only(self):
        index = fixture.KnowledgeIndex(fixture.Store(self.vault.parent / "legacy-data"), self.vault)
        legacy_dir = self.vault / "99-系统" / "知识库索引" / "资源组"
        legacy_dir.mkdir(parents=True)
        original_path = legacy_dir / "old.json"
        original = {"version": 1, "id": "legacy-bundle", "title": "旧合集", "folder": "02-知识/Skills",
                    "summary": "旧摘要", "members": ["02-知识/Skills/guide.md"], "directory": "", "entry": "", "active": True}
        original_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(index.read_bundles([])[0]["title"], "旧合集")
        changed = dict(original, title="数据库合集")
        index.save_bundle(changed)
        original_path.write_text(json.dumps(dict(original, title="外部改动"), ensure_ascii=False), encoding="utf-8")
        self.assertEqual(index.read_bundles([])[0]["title"], "数据库合集")
        self.assertTrue(original_path.is_file())
        index.create_bundle("新 SQLite 合集", members=["02-知识/Skills/guide.md"], folder="02-知识/Skills")
        self.assertIn("新 SQLite 合集", {item["title"] for item in index.read_bundles([])})
        self.assertEqual(sorted(path.name for path in legacy_dir.glob("*.json")), ["old.json"])

    def test_directory_collision_preserves_existing_target(self):
        source = self.directory_source()
        target = self.vault / "docs/package"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(fixture.UserError):
            self.page.index.import_directory(source, "docs")
        self.assertEqual((target / "keep.txt").read_text(), "keep")
        self.assertFalse(list((self.vault / "docs").glob(".*.tmp")))

    def test_directory_cancellation_cleans_partial_copy(self):
        source = self.directory_source()
        context = Mock()
        context.checkpoint.side_effect = [None, RuntimeError("cancelled")]
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            self.page.index.import_directory(source, "docs", context=context)
        self.assertFalse((self.vault / "docs/package").exists())
        self.assertFalse(list((self.vault / "docs").glob(".*.tmp")))
        self.assertEqual((source / "nested/data.bin").read_bytes(), bytes(range(256)))

    def test_directory_rejects_recursive_target_and_registration_failure_rolls_back(self):
        source = self.directory_source()
        with self.assertRaises(fixture.UserError):
            self.page.index.import_directory(self.vault, "docs")
        with patch.object(self.page.index, "create_bundle", side_effect=OSError("cannot register")):
            with self.assertRaisesRegex(OSError, "cannot register"):
                self.page.index.import_directory(source, "docs")
        self.assertFalse((self.vault / "docs/package").exists())
        self.assertFalse(list((self.vault / "docs").glob(".*.tmp")))

    def test_directory_import_reports_partial_copy_if_cleanup_fails(self):
        source = self.directory_source()
        target = self.vault / "docs/package"
        with patch.object(self.page.index, "create_bundle", side_effect=OSError("register failed")), \
                patch("selfManger.knowledge.shutil.rmtree", side_effect=PermissionError("locked copy")):
            with self.assertRaises(fixture.UserError) as raised:
                self.page.index.import_directory(source, "docs")
        self.assertIn(str(target), str(raised.exception))
        self.assertIn("PermissionError", str(raised.exception))
        self.assertIsInstance(raised.exception.__cause__, OSError)
        self.assertTrue(target.is_dir())

    def test_drop_files_asks_before_saving(self):
        source = self.vault / "picture.png"
        event = Mock()
        from PySide6.QtCore import QUrl
        event.mimeData.return_value.urls.return_value = [QUrl.fromLocalFile(str(source))]
        with patch.object(self.page, "collect_resource") as collect:
            self.page.drop_files(event)
            collect.assert_called_once_with("file", sources=[source.as_posix()], project_id="",
                                            default_folder="02-知识/00-收件箱")

if __name__ == "__main__":
    unittest.main()
