from __future__ import annotations

from pathlib import Path

import pytest

openpyxl = pytest.importorskip("openpyxl")

from price_compare import model_templates
from price_compare.model_templates import ModelTemplatesEditor, generate_model_pattern


def _make_editor_with_data(tmp_path: Path) -> ModelTemplatesEditor:
    editor = ModelTemplatesEditor(tmp_path / "models.json")
    editor.data = {
        "categories": [
            {
                "name": "Смартфони",
                "tags": ["Phones", "Мобільні"],
                "brands": [
                    {
                        "name": "BrandX",
                        "tags": ["BrandX"],
                        "models": [
                            {
                                "name": "Alpha",
                                "pattern": "pattern-alpha",
                                "tags": ["Phones", "BrandX", "Alpha", "Special"],
                            },
                            {
                                "name": "Beta",
                                "tags": ["Phones", "BrandX", "Beta"],
                            },
                        ],
                    }
                ],
            }
        ]
    }
    return editor


def test_export_and_import_models_excel_roundtrip(tmp_path: Path) -> None:
    editor = _make_editor_with_data(tmp_path)
    output = tmp_path / "models.xlsx"
    editor.export_to_excel(output)

    workbook = openpyxl.load_workbook(output, read_only=True, data_only=True)
    try:
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
    finally:
        workbook.close()

    assert rows[0][: len(model_templates._EXCEL_HEADERS)] == model_templates._EXCEL_HEADERS
    assert rows[1][0] == "Смартфони"
    assert rows[1][2] == "BrandX"
    assert rows[1][4] == "Alpha"
    assert "Special" in (rows[1][5] or "")
    expected_beta_pattern = generate_model_pattern("BrandX", "Beta")
    assert rows[2][6] == expected_beta_pattern

    imported = ModelTemplatesEditor(tmp_path / "imported.json")
    imported.import_from_excel(output)

    categories = imported.data["categories"]
    assert len(categories) == 1
    category = categories[0]
    assert category["name"] == "Смартфони"
    assert category["tags"] == ["Phones", "Мобільні"]

    brands = category["brands"]
    assert len(brands) == 1
    brand = brands[0]
    assert brand["name"] == "BrandX"
    assert brand["tags"] == ["BrandX"]

    models = {model["name"]: model for model in brand["models"]}
    assert set(models) == {"Alpha", "Beta"}
    assert models["Alpha"]["pattern"] == "pattern-alpha"
    assert models["Alpha"]["tags"] == ["Phones", "BrandX", "Alpha", "Special"]
    assert models["Beta"]["pattern"] == expected_beta_pattern
    assert models["Beta"]["tags"] == ["Phones", "BrandX", "Beta"]


def test_import_models_excel_detects_duplicates(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Models"
    sheet.append(model_templates._EXCEL_HEADERS)
    sheet.append(["Категорія", "", "BrandX", "", "Alpha", "", ""])
    sheet.append(["Категорія", "", "BrandX", "", "Alpha", "", ""])
    workbook.save(path)
    workbook.close()

    editor = ModelTemplatesEditor(tmp_path / "base.json")
    with pytest.raises(ValueError, match="дублюється"):
        editor.import_from_excel(path)
