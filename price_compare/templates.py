"""Import template storage utilities."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional


@dataclass
class ImportTemplate:
    """Represents saved import settings for a supplier."""

    supplier: str
    column_mapping: Dict[str, str]
    headers: List[str]


class ImportTemplateStore:
    """Persist import templates on disk for re-use."""

    def __init__(self, data_dir: str | os.PathLike[str]) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "import_templates.json"

    def _key(self, supplier: str) -> str:
        return supplier.strip().lower()

    def _load_all(self) -> Dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        templates = payload.get("templates", payload)
        if not isinstance(templates, dict):
            return {}
        return templates

    def _write_all(self, payload: Dict[str, dict]) -> None:
        to_dump = {"templates": payload}
        with self.path.open("w", encoding="utf-8") as fp:
            json.dump(to_dump, fp, indent=2, ensure_ascii=False)

    def list_templates(self) -> List[ImportTemplate]:
        templates: List[ImportTemplate] = []
        for key, value in self._load_all().items():
            if not isinstance(value, dict):
                continue
            supplier = value.get("supplier") or key
            column_mapping = value.get("column_mapping")
            headers = value.get("headers") or []
            if not isinstance(column_mapping, dict):
                continue
            templates.append(
                ImportTemplate(
                    supplier=str(supplier),
                    column_mapping={
                        str(field): str(column)
                        for field, column in column_mapping.items()
                        if str(column)
                    },
                    headers=[str(header) for header in headers if str(header)],
                )
            )
        templates.sort(key=lambda template: template.supplier.lower())
        return templates

    def get_template(self, supplier: str) -> Optional[ImportTemplate]:
        key = self._key(supplier)
        raw = self._load_all().get(key)
        if not isinstance(raw, dict):
            return None
        column_mapping = raw.get("column_mapping")
        if not isinstance(column_mapping, dict):
            return None
        headers = raw.get("headers") or []
        return ImportTemplate(
            supplier=raw.get("supplier") or supplier,
            column_mapping={
                str(field): str(column)
                for field, column in column_mapping.items()
                if str(column)
            },
            headers=[str(header) for header in headers if str(header)],
        )

    def save_template(
        self,
        supplier: str,
        column_mapping: Dict[str, str],
        headers: Iterable[str],
    ) -> None:
        key = self._key(supplier)
        payload = self._load_all()
        payload[key] = {
            "supplier": supplier,
            "column_mapping": dict(column_mapping),
            "headers": [str(header) for header in headers],
        }
        self._write_all(payload)

    def delete_template(self, supplier: str) -> None:
        key = self._key(supplier)
        payload = self._load_all()
        if key in payload:
            payload.pop(key)
            self._write_all(payload)
