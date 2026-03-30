from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any


@dataclass
class BirthdayRecord:
    name: str
    day: int
    month: int
    year: int | None = None


class BirthdayStorage:
    def __init__(self, file_path: str = "birthdays.json") -> None:
        self._file_path = Path(file_path)
        self._lock = RLock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self._file_path.exists():
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            self._file_path.write_text("{}", encoding="utf-8")

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        with self._lock:
            raw_text = self._file_path.read_text(encoding="utf-8").strip()
            if not raw_text:
                return {}

            try:
                data: dict[str, list[dict[str, Any]]] = json.loads(raw_text)
            except json.JSONDecodeError:
                data = {}
            return data

    def _save(self, data: dict[str, list[dict[str, Any]]]) -> None:
        with self._lock:
            self._file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def add_birthday(self, user_id: int, record: BirthdayRecord) -> bool:
        user_key = str(user_id)
        data = self._load()
        records = data.get(user_key, [])

        lower_name = record.name.casefold()
        if any(item.get("name", "").casefold() == lower_name for item in records):
            return False

        records.append(asdict(record))
        data[user_key] = records
        self._save(data)
        return True

    def remove_birthday(self, user_id: int, name: str) -> bool:
        user_key = str(user_id)
        data = self._load()
        records = data.get(user_key, [])
        lower_name = name.casefold()

        filtered = [
            item for item in records if item.get("name", "").casefold() != lower_name
        ]
        if len(filtered) == len(records):
            return False

        if filtered:
            data[user_key] = filtered
        else:
            data.pop(user_key, None)
        self._save(data)
        return True

    def get_user_birthdays(self, user_id: int) -> list[BirthdayRecord]:
        user_key = str(user_id)
        data = self._load()
        records = data.get(user_key, [])
        return [BirthdayRecord(**item) for item in records]

    def get_all_users(self) -> dict[int, list[BirthdayRecord]]:
        data = self._load()
        result: dict[int, list[BirthdayRecord]] = {}
        for user_id, records in data.items():
            result[int(user_id)] = [BirthdayRecord(**item) for item in records]
        return result
