"""Comparison logic for products across suppliers."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence

from .models import PriceList, Product


_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


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
            offers.extend(price_list.offers_for_sku(sku_lower))
        if offers:
            name = offers[0].name
        if not offers:
            return None
        offers.sort(key=lambda product: product.price)
        return ComparisonEntry(sku=sku, name=name, offers=offers)

    def compare_by_name(self, name: str, *, threshold: float = 0.75) -> List[ComparisonEntry]:
        name_lower = name.lower()
        offers_by_sku: Dict[str, List[Product]] = defaultdict(list)
        canonical_names: Dict[str, str] = {}
        canonical_skus: Dict[str, str] = {}
        query_tokens = set(_WORD_PATTERN.findall(name_lower))

        for price_list in self.price_lists:
            candidates = price_list.candidates_for_tokens(query_tokens)
            for product in candidates:
                similarity = self._name_similarity(query_tokens, product.name_tokens)
                if similarity >= threshold:
                    key = product.sku_lower or product.sku
                    offers_by_sku[key].append(product)
                    canonical_names.setdefault(key, product.name)
                    canonical_skus.setdefault(key, product.sku)

        entries = [
            ComparisonEntry(
                sku=canonical_skus.get(sku, sku),
                name=canonical_names[sku],
                offers=sorted(products, key=lambda p: p.price),
            )
            for sku, products in offers_by_sku.items()
        ]
        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    def best_offers(self) -> List[ComparisonEntry]:
        offers_by_sku: Dict[str, List[Product]] = defaultdict(list)
        names: Dict[str, str] = {}
        canonical_skus: Dict[str, str] = {}

        for price_list in self.price_lists:
            for sku, offers in price_list.iter_grouped_offers():
                key = sku.lower()
                offers_by_sku[key].extend(offers)
                if offers:
                    names.setdefault(key, offers[0].name)
                    canonical_skus.setdefault(key, offers[0].sku)

        entries = [
            ComparisonEntry(
                sku=canonical_skus.get(sku, sku),
                name=names[sku],
                offers=sorted(products, key=lambda p: p.price),
            )
            for sku, products in offers_by_sku.items()
        ]
        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    def _name_similarity(self, left_tokens: set[str], right_tokens: set[str]) -> float:
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = left_tokens & right_tokens
        union = left_tokens | right_tokens
        return len(intersection) / len(union)
