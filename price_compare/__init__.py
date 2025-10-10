"""Price comparison toolkit package."""

from .models import Product, PriceList
from .repository import PriceListRepository
from .tagging import TagRule, Tagger
from .comparator import PriceComparator
from .gui import PriceCompareApp, run_app
from .io import PriceListImporter, PriceListExporter
from .search import ProductSearch
from .synonyms_manager import SynonymsManager, open_synonyms_window

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
