"""Search utilities for products across suppliers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Dict, Iterator, List, Sequence, Set

from .models import PriceList, Product


@dataclass
class SearchResult:
    product: Product
    score: float


class ProductSearch:
    """Search for products using substring or fuzzy matching."""

    def __init__(self, price_lists: Sequence[PriceList]) -> None:
        self.price_lists = list(price_lists)
        self._product_by_id: Dict[int, Product] = {}
        self._supplier_index: Dict[int, str] = {}
        self._sku_index: Dict[str, Set[int]] = defaultdict(set)
        self._token_index: Dict[str, Set[int]] = defaultdict(set)
        for price_list in self.price_lists:
            supplier_lower = price_list.supplier_lower
            for product in price_list.products:
                self._register_product(product, supplier_lower)
        self._fuzzy_cache: Dict[tuple[str, int], float] = {}

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
        results: List[SearchResult] = []
        supplier_lower = supplier.lower() if supplier else None

        candidate_ids = self._collect_candidate_ids(query_lower)
        candidates = self._iter_candidates(candidate_ids, supplier_lower)

        if fuzzy:
            for product in candidates:
                score = self._fuzzy_score(query_lower, product)
                if score >= threshold:
                    results.append(SearchResult(product=product, score=score))
        else:
            for product in candidates:
                if query_lower in product.search_haystack:
                    results.append(SearchResult(product=product, score=1.0))

        results.sort(key=lambda result: (-result.score, result.product.price))
        return results[:limit]

    # ------------------------------------------------------------------
    # Internal helpers

    def _register_product(self, product: Product, supplier_lower: str) -> None:
        self._product_by_id[product.product_id] = product
        self._supplier_index[product.product_id] = supplier_lower
        self._sku_index[product.sku_lower].add(product.product_id)
        for token in product.search_tokens:
            if token:
                self._token_index[token].add(product.product_id)

    def _iter_candidates(
        self, candidate_ids: Set[int], supplier_lower: str | None
    ) -> Iterator[Product]:
        if candidate_ids:
            yielded = False
            for product_id in candidate_ids:
                if supplier_lower and self._supplier_index[product_id] != supplier_lower:
                    continue
                yielded = True
                yield self._product_by_id[product_id]
            if yielded:
                return

        if supplier_lower is None:
            yield from self._product_by_id.values()
        else:
            for product_id, product in self._product_by_id.items():
                if self._supplier_index[product_id] == supplier_lower:
                    yield product

    def _collect_candidate_ids(self, query_lower: str) -> Set[int]:
        candidate_ids: Set[int] = set()
        if not query_lower:
            return candidate_ids

        candidate_ids.update(self._sku_index.get(query_lower, set()))
        for token in self._tokens_for_query(query_lower):
            candidate_ids.update(self._token_index.get(token, set()))
        if not candidate_ids and " " in query_lower:
            for part in (segment for segment in query_lower.split() if segment):
                candidate_ids.update(self._token_index.get(part, set()))
        return candidate_ids

    @lru_cache(maxsize=2048)
    def _tokens_for_query(self, query: str) -> tuple[str, ...]:
        if not query:
            return tuple()
        return tuple(token for token in self._token_index if query in token)

    def _fuzzy_score(self, query: str, product: Product) -> float:
        key = (query, product.product_id)
        if key in self._fuzzy_cache:
            return self._fuzzy_cache[key]

        scores = [SequenceMatcher(None, query, token).ratio() for token in product.search_tokens]
        if product.search_haystack:
            scores.append(SequenceMatcher(None, query, product.search_haystack).ratio())
        score = max(scores) if scores else 0.0

        if len(self._fuzzy_cache) > 4096:
            self._fuzzy_cache.clear()
        self._fuzzy_cache[key] = score
        return score
