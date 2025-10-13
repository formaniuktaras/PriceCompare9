from __future__ import annotations

from pathlib import Path
import importlib.util
import sys
import types


def _load_simple_yaml() -> types.ModuleType:
    module_path = Path(__file__).resolve().parent.parent / "price_compare" / "simple_yaml.py"
    spec = importlib.util.spec_from_file_location("_pc_simple_yaml", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)  # type: ignore[attr-defined]
    return module


def test_safe_load_handles_project_config() -> None:
    simple_yaml = _load_simple_yaml()

    config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    data = config_path.read_text(encoding="utf-8")

    parsed = simple_yaml.safe_load(data)

    assert parsed["data_root"] == "data"
    assert parsed["sheets"]["spreadsheet_id"] == "<<<ВСТАВИТИ_ID_ТАБЛИЦІ>>>"
    suppliers = parsed["suppliers"]
    assert isinstance(suppliers, list)
    assert suppliers[0]["key"] == "itsellopt"
    assert suppliers[0]["paths"] == ["samples/itsellopt.csv", "samples/itsellopt.xlsx"]
