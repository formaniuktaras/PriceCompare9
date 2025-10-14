"""Comparison logic for products across suppliers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence, Set

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
        self._sku_index: Dict[str, List[Product]] = defaultdict(list)
        self._sku_tokens: Dict[str, Set[str]] = defaultdict(set)
        self._canonical_name: Dict[str, str] = {}
        self._build_indexes()

    def compare_by_sku(self, sku: str) -> ComparisonEntry | None:
        sku_lower = sku.lower()
        offers = self._sku_index.get(sku_lower)
        if not offers:
            return None
        offers_sorted = sorted(offers, key=lambda product: product.price)
        canonical = offers_sorted[0]
        return ComparisonEntry(sku=canonical.sku, name=canonical.name, offers=offers_sorted)

    def compare_by_name(self, name: str, *, threshold: float = 0.75) -> List[ComparisonEntry]:
        name_lower = name.lower().strip()
        query_tokens = {token for token in name_lower.split() if token}
        candidate_skus = self._candidate_skus(query_tokens)

        entries: List[ComparisonEntry] = []
        for sku_lower in candidate_skus:
            offers = self._sku_index.get(sku_lower)
            if not offers:
                continue
            canonical = offers[0]
            tokens = self._sku_tokens.get(sku_lower, set())
            similarity = self._name_similarity(query_tokens, tokens)
            if similarity >= threshold:
                sorted_offers = sorted(offers, key=lambda product: product.price)
                entries.append(
                    ComparisonEntry(
                        sku=canonical.sku,
                        name=self._canonical_name.get(sku_lower, canonical.name),
                        offers=sorted_offers,
                    )
                )

        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    def best_offers(self) -> List[ComparisonEntry]:
        entries: List[ComparisonEntry] = []
        for sku_lower, offers in self._sku_index.items():
            if not offers:
                continue
            sorted_offers = sorted(offers, key=lambda product: product.price)
            canonical = sorted_offers[0]
            entries.append(
                ComparisonEntry(
                    sku=canonical.sku,
                    name=self._canonical_name.get(sku_lower, canonical.name),
                    offers=sorted_offers,
                )
            )
        entries.sort(key=lambda entry: entry.best_offer.price if entry.best_offer else float("inf"))
        return entries

    # ------------------------------------------------------------------
    # Internal helpers

    def _build_indexes(self) -> None:
        for price_list in self.price_lists:
            for sku_lower, offers in price_list.iter_offers_by_sku():
                offer_list = self._sku_index[sku_lower]
                offer_list.extend(offers)
                # Track canonical name from the cheapest offer in the current list.
                cheapest = min(offers, key=lambda product: product.price)
                self._canonical_name.setdefault(sku_lower, cheapest.name)
                for product in offers:
                    tokens = {token for token in product.name_lower.split() if token}
                    if not tokens and product.name_lower:
                        tokens = set(product.name_lower.split())
                    self._sku_tokens[sku_lower].update(tokens)

        for offers in self._sku_index.values():
            offers.sort(key=lambda product: product.price)

        for sku_lower, name in list(self._canonical_name.items()):
            if not name:
                canonical_offers = self._sku_index.get(sku_lower, [])
                if canonical_offers:
                    self._canonical_name[sku_lower] = canonical_offers[0].name

        for sku_lower, tokens in list(self._sku_tokens.items()):
            if not tokens:
                canonical_offers = self._sku_index.get(sku_lower, [])
                if canonical_offers:
                    canonical_tokens = {
                        token
                        for token in canonical_offers[0].name_lower.split()
                        if token
                    }
                    self._sku_tokens[sku_lower] = canonical_tokens

    def _candidate_skus(self, query_tokens: Set[str]) -> Set[str]:
        if not query_tokens:
            return set(self._sku_index.keys())
        candidates: Set[str] = set()
        for sku_lower, tokens in self._sku_tokens.items():
            if query_tokens & tokens:
                candidates.add(sku_lower)
        if not candidates:
            return set(self._sku_index.keys())
        return candidates

    @staticmethod
    def _name_similarity(left_tokens: Set[str], right_tokens: Set[str]) -> float:
        if not left_tokens or not right_tokens:
            return 0.0
        intersection = left_tokens & right_tokens
        union = left_tokens | right_tokens
        return len(intersection) / len(union)
