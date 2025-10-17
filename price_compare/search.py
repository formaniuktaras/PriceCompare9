"""Search utilities for products across suppliers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import List, Sequence, Set

from .models import PriceList, Product


_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


@dataclass
class SearchResult:
    product: Product
    score: float


class ProductSearch:
    """Search for products using substring or fuzzy matching."""

    def __init__(self, price_lists: Sequence[PriceList]) -> None:
        self.price_lists = list(price_lists)
        self._sku_index: dict[str, Set[Product]] = {}
        for price_list in self.price_lists:
            for product in price_list.products:
                self._register_product(product)

    def _register_product(self, product: Product) -> None:
        if product.sku_lower:
            self._sku_index.setdefault(product.sku_lower, set()).add(product)

    def search(
        self,
        query: str,
        *,
        supplier: str | None = None,
        fuzzy: bool = False,
        limit: int = 20,
        threshold: float = 0.6,
    ) -> List[SearchResult]:
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        supplier_lists = self._filter_suppliers(supplier)
        query_tokens = tuple(_WORD_PATTERN.findall(query_lower))
        candidates = self._collect_candidates(query_lower, query_tokens, supplier_lists)
        if not candidates:
            return []

        results: List[SearchResult] = []
        token_set = set(query_tokens)
        if fuzzy:
            for product in candidates:
                score = self._fuzzy_score(query_lower, product)
                if score >= threshold:
                    results.append(SearchResult(product=product, score=score))
        else:
            for product in candidates:
                if query_lower in product.haystack_lower or (
                    token_set and token_set.issubset(product.search_token_set)
                ):
                    results.append(SearchResult(product=product, score=1.0))

        results.sort(key=lambda result: (-result.score, result.product.price))
        return results[:limit]

    def _filter_suppliers(self, supplier: str | None) -> Sequence[PriceList]:
        if not supplier:
            return self.price_lists
        supplier_lower = supplier.lower()
        return [
            price_list
            for price_list in self.price_lists
            if price_list.supplier.lower() == supplier_lower
        ]

    def _collect_candidates(
        self,
        query_lower: str,
        query_tokens: Sequence[str],
        price_lists: Sequence[PriceList],
    ) -> Set[Product]:
        candidates: Set[Product] = set()

        if query_lower in self._sku_index:
            candidates.update(self._sku_index[query_lower])

        if query_tokens:
            for price_list in price_lists:
                candidates.update(price_list.candidates_for_tokens(query_tokens))
        else:
            for price_list in price_lists:
                candidates.update(price_list.products)

        if not candidates and query_tokens:
            for price_list in price_lists:
                candidates.update(price_list.products)

        return candidates

    @staticmethod
    @lru_cache(maxsize=8192)
    def _compute_fuzzy_score(
        query: str, cache_key: str, haystack_lower: str, tokens: tuple[str, ...]
    ) -> float:
        scores = [SequenceMatcher(None, query, token).ratio() for token in tokens]
        if haystack_lower:
            scores.append(SequenceMatcher(None, query, haystack_lower).ratio())
        return max(scores) if scores else 0.0

    def _fuzzy_score(self, query: str, product: Product) -> float:
        return self._compute_fuzzy_score(
            query,
            product.cache_key,
            product.haystack_lower,
            product.search_tokens,
        )
