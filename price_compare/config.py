"""Configuration loading utilities for the ETL pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import yaml


@dataclass(frozen=True)
class SupplierConfig:
    """Configuration for a single supplier source."""

    key: str
    paths: tuple[Path, ...]


@dataclass(frozen=True)
class MatchingConfig:
    """Matching thresholds and aliases used downstream."""

    brand_aliases: dict[str, str]
    fuzzy_thresholds: dict[str, int]


@dataclass(frozen=True)
class AppConfig:
    """Top level configuration container."""

    data_root: Path
    suppliers: tuple[SupplierConfig, ...]
    matching: MatchingConfig

    def iter_supplier_jobs(self) -> Iterable[tuple[str, Path]]:
        """Yield (supplier_key, path) tuples for import jobs."""

        for supplier in self.suppliers:
            for path in supplier.paths:
                yield supplier.key, path


def _default_config_path() -> Path:
    """Return the default location of the configuration file."""

    package_root = Path(__file__).resolve().parent
    project_root = package_root.parent
    return project_root / "config.yaml"


@lru_cache(maxsize=1)
def load_config(path: str | Path | None = None) -> AppConfig:
    """Load the pipeline configuration from YAML."""

    config_path = Path(path) if path else _default_config_path()
    with config_path.open("r", encoding="utf-8") as fh:
        payload = yaml.safe_load(fh)

    base_dir = config_path.parent

    data_root_raw = Path(payload.get("data_root", "data"))
    data_root_path = data_root_raw if data_root_raw.is_absolute() else (base_dir / data_root_raw)
    data_root = data_root_path.resolve()
    suppliers_raw = payload.get("suppliers", [])
    suppliers = []
    for entry in suppliers_raw:
        key = entry["key"]
        raw_paths = entry.get("paths", [])
        resolved_paths = []
        for item in raw_paths:
            p = Path(item)
            if not p.is_absolute():
                p = (base_dir / p).resolve()
            else:
                p = p.resolve()
            resolved_paths.append(p)
        suppliers.append(SupplierConfig(key=key, paths=tuple(resolved_paths)))

    matching_raw = payload.get("matching", {})
    matching = MatchingConfig(
        brand_aliases={str(k): str(v) for k, v in matching_raw.get("brand_aliases", {}).items()},
        fuzzy_thresholds={str(k): int(v) for k, v in matching_raw.get("fuzzy_thresholds", {}).items()},
    )

    return AppConfig(data_root=data_root, suppliers=tuple(suppliers), matching=matching)


def data_root() -> Path:
    """Convenience accessor for the configured data root directory."""

    cfg = load_config()
    cfg.data_root.mkdir(parents=True, exist_ok=True)
    return cfg.data_root
