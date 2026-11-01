from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

from .domain import UserError
from .knowledge import KnowledgeIndex


@dataclass(frozen=True)
class KnowledgeCollectionRequest:
    kind: Literal["link", "file", "note", "directory"]
    folder: str
    title: str
    summary: str
    tags: tuple[str, ...]
    project_id: str
    agent_id: str
    url: str
    body: str
    sources: tuple[str, ...]
    copy_files: bool
    combine_files: bool
    directory_source: str


class KnowledgeCollectionResult(TypedDict):
    added: int
    preferred: str
    folder: str
    errors: list[str]


class CheckpointContext(Protocol):
    def checkpoint(self) -> None: ...


def _safe_name(value: str, fallback: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", value).strip(" .")[:160] or fallback
    if name.upper().split(".")[0] in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        return "_" + name
    return name


def _unique_stem(directory: Path, stem: str, suffix: str) -> str:
    candidate, number = stem, 2
    while (directory / (candidate + suffix)).exists():
        candidate = f"{stem} ({number})"
        number += 1
    return candidate


def collect_knowledge_resource(index: KnowledgeIndex, request: KnowledgeCollectionRequest,
                               context: CheckpointContext | None = None) -> KnowledgeCollectionResult:
    """Persist one knowledge collection request without depending on Qt widgets."""
    if request.kind not in {"link", "file", "note", "directory"}:
        raise UserError("资料类型无效，无法保存。")
    if request.kind == "directory" and not request.directory_source:
        raise UserError("请选择要导入的资料目录。")
    if request.kind == "file" and not request.sources:
        raise UserError("请选择要收集的文件。")

    folder_path = index.root / request.folder
    if not folder_path.is_dir():
        index.create_folder(request.folder)

    if request.kind == "directory":
        relative = index.import_directory(
            request.directory_source,
            request.folder,
            title=request.title,
            summary=request.summary,
            context=context,
        )
        index.update_metadata(
            relative,
            tags=list(request.tags),
            projects=[request.project_id] if request.project_id else [],
            agents=[request.agent_id] if request.agent_id else [],
        )
        return {"added": 1, "preferred": relative, "folder": request.folder, "errors": []}

    errors: list[str] = []
    preferred = ""
    count = 0
    file_folder = request.folder
    if request.kind == "file" and request.combine_files and len(request.sources) > 1:
        folder_name = _safe_name(request.title, "收集的资料")
        candidate = _unique_stem(index.root / file_folder, folder_name, "")
        file_folder = (Path(file_folder) / candidate).as_posix()
        index.create_folder(file_folder)

    inputs = request.sources if request.kind == "file" else ("",)
    for source in inputs:
        if context:
            context.checkpoint()
        try:
            physical_name = _safe_name(request.title, "资源")
            if request.kind in {"link", "note"}:
                extension = ".url" if request.kind == "link" else ".md"
                physical_name = _unique_stem(index.root / file_folder, physical_name, extension)
            if request.kind == "link":
                path = index.add_url(physical_name, request.url, file_folder)
            elif request.kind == "note":
                path = index.add_note(physical_name, request.body, file_folder)
            else:
                path = index.add_file(source, file_folder, copy=request.copy_files, context=context)
            relative = (
                "external:" + json.dumps(str(path), ensure_ascii=False)
                if request.kind == "file" and not request.copy_files
                else Path(path).relative_to(index.root).as_posix()
            )
            index.update_metadata(
                relative,
                tags=list(request.tags),
                projects=[request.project_id] if request.project_id else [],
                agents=[request.agent_id] if request.agent_id else [],
            )
            index.update_resource(
                relative,
                title=request.title if len(inputs) == 1 else "",
                summary=request.summary,
            )
            preferred, count = relative, count + 1
        except (OSError, ValueError, UserError) as exc:
            errors.append((Path(source).name + "：" if source else "") + str(exc))

    if count == 0 and file_folder != request.folder:
        try:
            index.delete_empty_folder(file_folder)
        except (OSError, UserError) as exc:
            errors.append("空收集文件夹清理失败：" + str(exc))
    return {"added": count, "preferred": preferred, "folder": file_folder, "errors": errors}
