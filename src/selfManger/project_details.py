"""Read-only project detail report assembly for the project page."""
from __future__ import annotations

from pathlib import Path

from .action_center import ActionCenter
from .domain import UserError
from .knowledge import KnowledgeIndex, knowledge_root
from .project_read_models import milestone_rows, overview_snapshot
from .project_workspaces import ProjectWorkspace
from .runtime import TaskContext
from .storage import Store
from .task_ordering import project_checklist_snapshot
from .workbench import project_context, summaries


class ProjectDetailReader:
    """Assemble the non-widget data used by a selected project detail view."""

    def __init__(self, store: Store, root: Path):
        self.store = store
        self.root = Path(root)

    def read(self, identity: str, state: str, document_paths: tuple[str, ...],
             context: TaskContext, *, record_use: bool) -> dict:
        workspace = ProjectWorkspace(self.root).read_snapshot()
        management = workspace.project_detail_files(identity, document_paths, context)
        documents, errors = management["documents"], list(management["errors"])
        milestones = management["milestones"]
        library_items = []
        notes_root = knowledge_root(self.store)
        if notes_root is not None:
            try:
                library_items = KnowledgeIndex(self.store, notes_root).project_resources(identity)
            except (UserError, OSError) as exc:
                errors.append("知识库：" + str(exc))

        status_text = documents[0]["text"]
        try:
            details = project_context(status_text, state)
        except UserError as exc:
            documents[0]["error"] = str(exc)
            errors.append("STATUS：" + str(exc))
            status_text = ""
            details = project_context("", state)
        blocks = summaries(documents[2]["text"])
        linked_tasks = ActionCenter(self.store).project_tasks(identity, str(workspace.root))
        snapshot = overview_snapshot(status_text, documents[2]["text"], blocks)
        snapshot = project_checklist_snapshot(snapshot, linked_tasks)
        context.checkpoint()

        used_at = ""
        if record_use:
            try:
                # Use a fresh registry for mutation; the read snapshot is immutable.
                used_at = ProjectWorkspace(self.root).record_project_use(identity)
            except (OSError, UserError) as exc:
                errors.append("未能更新项目使用顺序：" + str(exc))
        return {"documents": documents, "milestones": milestones, "knowledge_resources": library_items,
                "project_documents": management["project_documents"],
                "project_documents_error": management["project_documents_error"],
                "notes": management["notes"], "notes_error": management["notes_error"], "errors": errors,
                "details": details, "snapshot": snapshot, "summaries": blocks,
                "milestone_rows": milestone_rows(milestones.get("text", "")),
                "used_at": used_at, "linked_tasks": linked_tasks}
