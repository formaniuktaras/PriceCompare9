"""Price comparison toolkit package."""

from __future__ import annotations

import importlib

from .comparator import PriceComparator
from .io import PriceListExporter, PriceListImporter
from .models import PriceList, Product
from .repository import PriceListRepository
from .search import ProductSearch
from .synonyms_manager import SynonymsManager, open_synonyms_window
from .tagging import TagRule, Tagger

__all__ = [
    "Product",
    "PriceList",
    "PriceListRepository",
    "TagRule",
    "Tagger",
    "PriceComparator",
    "PriceCompareApp",
    "ProductSearch",
    "PriceListImporter",
    "PriceListExporter",
    "SynonymsManager",
    "open_synonyms_window",
    "run_app",
]


def __getattr__(name: str):  # pragma: no cover - exercised indirectly
    if name in {"PriceCompareApp", "run_app"}:
        module = importlib.import_module(".gui", __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name}")
