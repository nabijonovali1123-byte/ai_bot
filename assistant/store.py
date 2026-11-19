from __future__ import annotations

import json
import os
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional


def ensure_private_dir(path: Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(str(directory), 0o700)
    except OSError:
        pass
    return directory


def atomic_write_json(path: Path, value: Any) -> None:
    target = Path(path)
    ensure_private_dir(target.parent)
    fd, temporary = tempfile.mkstemp(prefix="." + target.name + ".", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        try:
            os.chmod(str(target), 0o600)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class JsonFile:
    def __init__(self, path: Path, default: Any) -> None:
        self.path = Path(path)
        self.default = default

    def read(self) -> Any:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError, TypeError):
            return self.default() if callable(self.default) else self.default

    def write(self, value: Any) -> None:
        atomic_write_json(self.path, value)


class HistoryStore:
    def __init__(self, path: Path, limit: int = 30) -> None:
        self.file = JsonFile(path, dict)
        self.limit = limit

    @staticmethod
    def key(connection_id: str, chat_id: int) -> str:
        return "{}:{}".format(connection_id, chat_id)

    def get(self, connection_id: str, chat_id: int) -> List[Dict[str, Any]]:
        value = self.file.read()
        return list(value.get(self.key(connection_id, chat_id), []))

    def add(self, connection_id: str, chat_id: int, *, role: str, name: str, text: str, lang: Optional[str], ts: Optional[float] = None) -> None:
        value = self.file.read()
        key = self.key(connection_id, chat_id)
        items = list(value.get(key, []))
        items.append({"role": role, "name": name, "text": text, "lang": lang, "ts": ts if ts is not None else time.time()})
        value[key] = items[-self.limit:]
        self.file.write(value)

    def last_language(self, connection_id: str, chat_id: int) -> Optional[str]:
        for item in reversed(self.get(connection_id, chat_id)):
            if item.get("lang"):
                return item["lang"]
        return None

    def chat_count(self) -> int:
        return len(self.file.read())


class SeenMessages:
    def __init__(self, path: Path, capacity: int = 5000) -> None:
        self.file = JsonFile(path, list)
        self.capacity = capacity
        self.items = OrderedDict((str(key), None) for key in self.file.read())

    def first_time(self, key: str) -> bool:
        if key in self.items:
            return False
        self.items[key] = None
        while len(self.items) > self.capacity:
            self.items.popitem(last=False)
        self.file.write(list(self.items.keys()))
        return True


class RuntimeState:
    def __init__(self, path: Path) -> None:
        self.file = JsonFile(path, lambda: {"paused": False, "limit_until": 0})

    @property
    def value(self) -> Dict[str, Any]:
        data = self.file.read()
        return {"paused": bool(data.get("paused", False)), "limit_until": float(data.get("limit_until", 0) or 0)}

    def update(self, **values: Any) -> None:
        data = self.value
        data.update(values)
        self.file.write(data)

    def clear(self) -> None:
        self.file.write({"paused": False, "limit_until": 0})
