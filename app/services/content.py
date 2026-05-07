from __future__ import annotations

import json
from pathlib import Path
from string import Formatter
from typing import Any

from app.config import Settings


class SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


class ContentService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._messages = self._load_messages(Path(settings.content_file))

    def get(self, key: str, default: str = "", **values: Any) -> str:
        template = self._resolve(key)
        if template is None:
            template = default
        return format_message(str(template), **values)

    def has(self, key: str) -> bool:
        return self._resolve(key) is not None

    def _resolve(self, key: str) -> Any:
        current: Any = self._messages
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _load_messages(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))


def format_message(template: str, **values: Any) -> str:
    normalized = SafeDict({key: "" if value is None else value for key, value in values.items()})
    field_names = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    for field_name in field_names:
        normalized.setdefault(field_name, "{" + field_name + "}")
    return template.format_map(normalized)
