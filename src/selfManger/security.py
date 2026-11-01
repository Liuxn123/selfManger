from __future__ import annotations

import re
import json
import os
import threading
from pathlib import Path
from typing import Any

from .domain import UserError

SENSITIVE = re.compile(r"(?i)(password|passphrase|secret|token|api[_-]?key|access[_-]?key|authorization|credential)")


def redact(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----", "[已隐藏私钥]", text)
    text = re.sub(r"(?i)(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{12,})", "[已隐藏凭据]", text)
    keys = r"password|passphrase|secret|token|api[_-]?key|access[_-]?key|authorization|credential|ticket"
    # Quoted JSON/YAML values may contain spaces; HTTP headers include a scheme.
    text = re.sub(r"(?i)(\bauthorization[\"']?\s*[=:]\s*)[^\r\n]+", r"\1[已隐藏]", text)
    text = re.sub(r"(?i)((?:" + keys + r")[\"']?\s*[=:]\s*)(?:\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;&\]\}\"']+)", r"\1[已隐藏]", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[已隐藏]@", text)
    return text[:40000]


def safe_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): ("[已隐藏]" if SENSITIVE.search(str(key)) and not str(key).endswith(("_present", "_count", "_available", "_restored")) else safe_result(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_result(item) for item in value[:500]]
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, (int, float, bool, type(None))):
        return value
    return redact(str(value))


class SecretStore:
    service = "selfManger"

    def __init__(self, portable_root: Path | None = None) -> None:
        self._lock = threading.RLock()
        self.vault = portable_root / "data/credentials.enc" if portable_root else None
        self._key: bytes | None = None
        self._salt: bytes | None = None
        self._values: dict[str, str] = {}

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    def unlock(self, password: str) -> None:
        if not self.vault:
            raise UserError("当前使用系统凭据存储。")
        if len(password) < 8:
            raise UserError("便携口令库的主口令至少需要 8 个字符，请单独保存。")
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.exceptions import InvalidTag
        data = self.vault.read_bytes() if self.vault.is_file() else b""
        if data and (len(data) < 48 or len(data) > 2_000_000 or not data.startswith(b"AMVAULT1")):
            raise UserError("便携口令库格式损坏。")
        salt = data[8:24] if data else os.urandom(16)
        key = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1).derive(password.encode("utf-8"))
        try:
            values = json.loads(AESGCM(key).decrypt(data[24:36], data[36:], b"AMVAULT1")) if data else {}
        except (InvalidTag, ValueError) as exc:
            raise UserError("主口令不正确，便携口令库未解锁。") from exc
        if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
            raise UserError("便携口令库内容不正确。")
        self._key, self._salt, self._values = key, salt, values

    def lock(self) -> None:
        self._key = self._salt = None
        self._values.clear()

    def _write_vault(self, values: dict[str, str]) -> None:
        if not self.unlocked or not self.vault:
            raise UserError("请先在设置中解锁便携口令库，或只临时输入备份口令。")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.vault.parent.mkdir(parents=True, exist_ok=True)
        nonce = os.urandom(12)
        encrypted = AESGCM(self._key).encrypt(nonce, json.dumps(values).encode("utf-8"), b"AMVAULT1")
        temporary = self.vault.with_suffix(".partial")
        try:
            with temporary.open("wb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(b"AMVAULT1" + self._salt + nonce + encrypted)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(self.vault)
            self._values = values
        finally:
            temporary.unlink(missing_ok=True)

    def _backend(self):
        import keyring
        backend = keyring.get_keyring()
        module = type(backend).__module__
        if "keyrings.alt" in module or backend.priority <= 0:
            raise UserError("系统安全凭据存储不可用，请临时输入口令，或配置系统钥匙串。")
        return keyring

    def save(self, identity: str, value: str) -> None:
        if self.vault:
            with self._lock:
                self._write_vault({**self._values, identity: value})
            return
        try:
            self._backend().set_password(self.service, identity, value)
        except UserError:
            raise
        except Exception as exc:
            raise UserError("无法保存到系统凭据存储。请检查系统钥匙串是否已解锁。") from exc

    def get(self, identity: str) -> str | None:
        if self.vault:
            with self._lock:
                return self._values.get(identity) if self.unlocked else None
        try:
            return self._backend().get_password(self.service, identity)
        except UserError:
            raise
        except Exception as exc:
            raise UserError("无法读取系统凭据存储。") from exc

    def remove(self, identity: str) -> None:
        if self.vault:
            with self._lock:
                values = dict(self._values)
                values.pop(identity, None)
                self._write_vault(values)
            return
        try:
            self._backend().delete_password(self.service, identity)
        except UserError:
            raise
        except Exception as exc:
            raise UserError("无法移除系统凭据，请检查凭据是否存在。") from exc
