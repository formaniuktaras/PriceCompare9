"""Comparison logic for products across suppliers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .models import PriceList, Product


@dataclass
class ComparisonEntry:
    sku: str
    name: str
    offers: List[Product]

    @property
    def best_offer(self) -> Product | None:
        return min(self.offers, key=lambda product: product.price, default=None)


class PriceComparator:
    """Compare product prices across multiple suppliers."""

    def __init__(self, price_lists: Sequence[PriceList]) -> None:
        self.price_lists = list(price_lists)

    def compare_by_sku(self, sku: str) -> ComparisonEntry | None:
        sku_lower = sku.lower()
        offers: List[Product] = []
        name = ""
        for price_list in self.price_lists:
            for product in price_list.products:
                if product.sku.lower() == sku_lower:
                    offers.append(product)
                    name = product.name
        if not offers:
            return None
        offers.sort(key=lambda product: product.price)
        return ComparisonEntry(sku=sku, name=name, offers=offers)

    def compare_by_name(self, name: str, *, threshold: float = 0.75) -> List[ComparisonEntry]:
        name_lower = name.lower()
        offers_by_sku: Dict[str, List[Product]] = defaultdict(list)
        canonical_names: Dict[str, str] = {}

        for price_list in self.price_lists:
            for product in price_list.products:
                similarity = self._name_similarity(name_lower, product.name.lower())
                if similarity >= threshold:
                    offers_by_sku[product.sku].append(product)
                    canonical_names.setdefault(product.sku, product.name)

        entries = [
            ComparisonEntry(sku=sku, name=canonical_names[sku], offers=sorted(products, key=lambda p: p.price))
            for sku, products in offers_by_sku.items()
        ]
        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    def best_offers(self) -> List[ComparisonEntry]:
        offers_by_sku: Dict[str, List[Product]] = defaultdict(list)
        names: Dict[str, str] = {}

        for price_list in self.price_lists:
            for product in price_list.products:
                offers_by_sku[product.sku].append(product)
                names.setdefault(product.sku, product.name)

        entries = [
            ComparisonEntry(sku=sku, name=names[sku], offers=sorted(products, key=lambda p: p.price))
            for sku, products in offers_by_sku.items()
        ]
        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    def _name_similarity(self, left: str, right: str) -> float:
        left_tokens = set(left.split())
        right_tokens = set(right.split())
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = left_tokens & right_tokens
        union = left_tokens | right_tokens
        return len(intersection) / len(union)
