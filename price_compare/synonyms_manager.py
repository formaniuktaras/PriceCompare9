from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Dict, List


class SynonymsManager:
    def __init__(self, filename: str | os.PathLike[str] = "synonyms.json") -> None:
        self.filename = str(filename)
        self.synonyms: Dict[str, List[str]] = self.load_synonyms()

    def load_synonyms(self) -> Dict[str, List[str]]:
        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                return {}
            normalized: Dict[str, List[str]] = {}
            for canonical, variants in data.items():
                canonical_key = str(canonical).strip().lower()
                cleaned_variants = [
                    str(variant).strip().lower()
                    for variant in variants
                    if str(variant).strip()
                ]
                if canonical_key:
                    normalized[canonical_key] = list(dict.fromkeys(cleaned_variants))
            return normalized
        return {}

    def save_synonyms(self) -> None:
        path = Path(self.filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.synonyms, f, ensure_ascii=False, indent=2)

    def add_synonym(self, canonical: str, variant: str) -> None:
        canonical = canonical.strip().lower()
        variant = variant.strip().lower()
        if not canonical or not variant:
            return
        if canonical not in self.synonyms:
            self.synonyms[canonical] = []
        if variant not in self.synonyms[canonical]:
            self.synonyms[canonical].append(variant)

    def delete_synonym(self, canonical: str, variant: str | None = None) -> None:
        canonical = canonical.strip().lower()
        if canonical in self.synonyms:
            if variant:
                cleaned = variant.strip().lower()
                self.synonyms[canonical] = [
                    v for v in self.synonyms[canonical] if v != cleaned
                ]
                if not self.synonyms[canonical]:
                    del self.synonyms[canonical]
            else:
                del self.synonyms[canonical]
        self.save_synonyms()

    def normalize_text(self, text: str) -> str:
        text = text.lower()
        for canonical, variants in self.synonyms.items():
            for variant in variants:
                text = re.sub(
                    rf"\\b{re.escape(variant)}\\b", canonical, text, flags=re.IGNORECASE
                )
        return text.strip()

    def preprocess_product(self, product: Dict[str, str]) -> Dict[str, str]:
        name = product.get("name", "")
        desc = product.get("description", "")
        product["normalized_name"] = self.normalize_text(name)
        product["normalized_desc"] = self.normalize_text(desc)
        return product


def open_synonyms_window(filename: str | os.PathLike[str] = "synonyms.json") -> None:
    if importlib.util.find_spec("PySimpleGUI") is None:
        raise ModuleNotFoundError(
            "PySimpleGUI is required to open the synonyms editor. "
            "Install it with 'pip install PySimpleGUI'."
        )

    sg = importlib.import_module("PySimpleGUI")

    manager = SynonymsManager(filename)
    data = [[c, ", ".join(v)] for c, v in manager.synonyms.items()]
    table = sg.Table(
        values=data,
        headings=["Основне слово", "Синоніми"],
        auto_size_columns=False,
        col_widths=[25, 70],
        key="-TABLE-",
        justification="left",
        num_rows=15,
        enable_events=True,
    )

    layout = [
        [sg.Text("Синоніми для автоматичного нормалізування назв товарів")],
        [table],
        [
            sg.Text("Основне слово:"),
            sg.Input(key="-CANONICAL-", size=(20, 1)),
            sg.Text("Синонім:"),
            sg.Input(key="-VARIANT-", size=(30, 1)),
            sg.Button("Додати", key="-ADD-"),
            sg.Button("Видалити", key="-DEL-"),
        ],
        [sg.Button("Зберегти"), sg.Button("Закрити")],
    ]

    window = sg.Window("Синоніми", layout, modal=True, finalize=True)

    while True:
        event, values = window.read()
        if event in (None, "Закрити"):
            break
        elif event == "-ADD-":
            canonical = values["-CANONICAL-"].strip()
            variant = values["-VARIANT-"].strip()
            if canonical and variant:
                manager.add_synonym(canonical, variant)
                manager.save_synonyms()
                data = [[c, ", ".join(v)] for c, v in manager.synonyms.items()]
                window["-TABLE-"].update(values=data)
        elif event == "-DEL-":
            selected = values["-TABLE-"]
            if selected:
                canonical = data[selected[0]][0]
                manager.delete_synonym(canonical)
                data = [[c, ", ".join(v)] for c, v in manager.synonyms.items()]
                window["-TABLE-"].update(values=data)
        elif event == "Зберегти":
            manager.save_synonyms()

    window.close()


__all__ = ["SynonymsManager", "open_synonyms_window"]
