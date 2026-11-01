from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol
from uuid import uuid4

# Historical kinds remain parseable so existing registrations are never discarded.
KINDS = {"hermes_local": "本地 Hermes", "hermes_server": "服务器 Hermes", "project": "本地项目", "folder": "本地文件夹", "script": "脚本备份", "vault": "Obsidian", "agent": "Agent"}


class UserError(Exception):
    """An actionable error whose message may be shown to the user."""


class Cancelled(UserError):
    pass


@dataclass
class Resource:
    name: str
    kind: str
    options: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Resource:
        if data.get("kind") not in KINDS or not isinstance(data.get("options", {}), dict):
            raise UserError("资源类型或配置格式不正确。")
        name = str(data.get("name", "")).strip()
        identity = str(data.get("id", uuid4().hex))
        if not name or len(name) > 120 or not identity.isalnum() or len(identity) > 80:
            raise UserError("资源名称或标识不正确。")
        return cls(name, data["kind"], dict(data.get("options", {})), identity)


@dataclass
class RestorePlan:
    resource_id: str
    token: str
    source: str
    target: str
    summary: dict[str, Any]


class Context(Protocol):
    def log(self, message: str) -> None: ...
    def checkpoint(self) -> None: ...


class Adapter(Protocol):
    capabilities: frozenset[str]
    def observe(self, resource: Resource, context: Context) -> dict[str, Any]: ...


class AdapterRegistry:
    """Explicit, trusted registrations; UI discovers abilities from this contract."""

    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, kind: str, adapter: Adapter) -> None:
        if kind in self._adapters:
            raise ValueError(f"Adapter already registered: {kind}")
        self._adapters[kind] = adapter

    def get(self, resource: Resource) -> Adapter:
        return self.for_kind(resource.kind)

    def for_kind(self, kind: str) -> Adapter:
        try:
            return self._adapters[kind]
        except KeyError as exc:
            raise UserError("此资源尚未安装适配器。") from exc

    def describe(self) -> dict[str, list[str]]:
        return {kind: sorted(adapter.capabilities) for kind, adapter in self._adapters.items()}


Operation = Callable[[Context], dict[str, Any]]
