"""Search utilities for products across suppliers."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Sequence

from .models import PriceList, Product


@dataclass
class SearchResult:
    product: Product
    score: float


class ProductSearch:
    """Search for products using substring or fuzzy matching."""

    def __init__(self, price_lists: Sequence[PriceList]) -> None:
        self.price_lists = list(price_lists)

    def search(self, query: str, *, supplier: str | None = None, fuzzy: bool = False, limit: int = 20, threshold: float = 0.6) -> List[SearchResult]:
        query_lower = query.lower().strip()
        results: List[SearchResult] = []

        for price_list in self.price_lists:
            if supplier and price_list.supplier.lower() != supplier.lower():
                continue
            for product in price_list.products:
                if fuzzy:
                    score = self._fuzzy_score(query_lower, product)
                    if score >= threshold:
                        results.append(SearchResult(product=product, score=score))
                else:
                    if query_lower in product.name.lower() or query_lower in product.sku.lower():
                        results.append(SearchResult(product=product, score=1.0))

        results.sort(key=lambda result: (-result.score, result.product.price))
        return results[:limit]

    def _fuzzy_score(self, query: str, product: Product) -> float:
        haystack = " ".join(filter(None, [product.sku, product.name, product.description]))
        tokens = haystack.lower().split()
        scores = [SequenceMatcher(None, query, token).ratio() for token in tokens]
        scores.append(SequenceMatcher(None, query, haystack.lower()).ratio())
        return max(scores) if scores else 0.0
