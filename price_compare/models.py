"""Domain models for price comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import count
from typing import Dict, Iterable, Iterator, List, Sequence, Set


def _normalize_text(value: str | None) -> str:
    return value.strip().lower() if value else ""


@dataclass
class Product:
    """Represents a product entry coming from a supplier price list."""

    sku: str
    name: str
    price: float
    currency: str = "USD"
    description: str | None = None
    supplier: str | None = None
    tags: Set[str] = field(default_factory=set)
    extra: Dict[str, str] = field(default_factory=dict)

    # Cached normalized fields for faster search/comparison.
    product_id: int = field(init=False, repr=False)
    sku_lower: str = field(init=False, repr=False)
    name_lower: str = field(init=False, repr=False)
    search_haystack: str = field(init=False, repr=False)
    search_tokens: tuple[str, ...] = field(init=False, repr=False)

    _id_sequence = count()

    def matches_keyword(self, keyword: str) -> bool:
        """Check if the product matches the supplied keyword."""

        keyword_lower = keyword.lower().strip()
        if not keyword_lower:
            return False
        return keyword_lower in self.search_haystack

    def merge_tags(self, tags: Iterable[str]) -> None:
        """Merge a collection of tags into the product."""

        self.tags.update(tag.strip().lower() for tag in tags if tag)
        self._refresh_normalized_fields()

    # ------------------------------------------------------------------
    # Internal helpers

    def __post_init__(self) -> None:
        self.product_id = next(self._id_sequence)
        self._refresh_normalized_fields()

    def _refresh_normalized_fields(self) -> None:
        self.sku_lower = _normalize_text(self.sku)
        self.name_lower = _normalize_text(self.name)

        haystack_parts: list[str] = [self.sku_lower, self.name_lower]
        if self.description:
            haystack_parts.append(str(self.description).lower())
        if self.tags:
            haystack_parts.append(" ".join(sorted(tag.lower() for tag in self.tags)))

        # Collapse whitespace and deduplicate tokens while preserving order.
        haystack = " ".join(part for part in haystack_parts if part)
        self.search_haystack = " ".join(haystack.split())
        tokens: list[str] = []
        seen: set[str] = set()
        for token in self.search_haystack.split():
            if token not in seen:
                seen.add(token)
                tokens.append(token)
        self.search_tokens = tuple(tokens)


@dataclass
class PriceList:
    """Collection of products from a supplier."""

    supplier: str
    products: List[Product] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    # Internal indexes populated lazily.
    _sku_index: Dict[str, List[Product]] = field(default_factory=dict, init=False, repr=False)
    _token_index: Dict[str, Set[int]] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # Rebuild indexes to ensure deterministic order and cached lookups.
        self._rebuild_indexes()

    def add_product(self, product: Product) -> None:
        product.supplier = self.supplier
        product._refresh_normalized_fields()
        self.products.append(product)
        self._index_product(product)

    def by_sku(self) -> Dict[str, Product]:
        """Return a mapping of SKU -> product.

        If multiple products share the same SKU, the cheapest product is used.
        """

        return {offers[0].sku: offers[0] for offers in self._sku_index.values() if offers}

    def filter_by_keyword(self, keyword: str) -> List[Product]:
        """Return products that match the given keyword."""

        return [product for product in self.products if product.matches_keyword(keyword)]

    def sort_products(self) -> None:
        """Sort products in-place by SKU then by price."""

        self.products.sort(key=lambda product: (product.sku, product.price))
        self._rebuild_indexes()

    # ------------------------------------------------------------------
    # Index helpers

    @property
    def supplier_lower(self) -> str:
        return self.supplier.lower()

    def offers_for_sku(self, sku: str) -> List[Product]:
        return list(self._sku_index.get(sku.lower(), []))

    def tokens_index(self) -> Dict[str, Set[int]]:
        return self._token_index

    def iter_offers_by_sku(self) -> Iterator[tuple[str, Sequence[Product]]]:
        for sku, offers in self._sku_index.items():
            if offers:
                yield sku, tuple(offers)

    def _rebuild_indexes(self) -> None:
        self._sku_index.clear()
        self._token_index.clear()
        for product in self.products:
            product.supplier = self.supplier
            product._refresh_normalized_fields()
        # Ensure deterministic order for downstream consumers.
        self.products.sort(key=lambda product: (product.sku_lower, product.price))

        for product in self.products:
            self._index_product(product)

    def _index_product(self, product: Product) -> None:
        offers = self._sku_index.setdefault(product.sku_lower, [])
        # Maintain offers sorted by price.
        inserted = False
        for index, existing in enumerate(offers):
            if product.price < existing.price:
                offers.insert(index, product)
                inserted = True
                break
        if not inserted:
            offers.append(product)

        for token in product.search_tokens:
            if token:
                self._token_index.setdefault(token, set()).add(product.product_id)
