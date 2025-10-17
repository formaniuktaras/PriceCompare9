"""Domain models for price comparison."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, Iterable, List, Set


_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)


def _tokenize(*parts: str | None) -> tuple[str, ...]:
    """Return a tuple of unique lowercase tokens extracted from parts."""

    seen: set[str] = set()
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        for token in _WORD_PATTERN.findall(str(part).lower()):
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return tuple(tokens)


@dataclass(eq=False)
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

    # Search/index related caches -------------------------------------------------
    _sku_lower: str = field(init=False, repr=False)
    _name_lower: str = field(init=False, repr=False)
    _description_lower: str | None = field(init=False, repr=False)
    _search_tokens: tuple[str, ...] = field(init=False, repr=False)
    _search_token_set: FrozenSet[str] = field(init=False, repr=False)
    _haystack_lower: str = field(init=False, repr=False)
    _name_tokens: FrozenSet[str] = field(init=False, repr=False)
    _owner: "PriceList | None" = field(default=None, init=False, repr=False)

    def matches_keyword(self, keyword: str) -> bool:
        """Check if the product matches the supplied keyword."""

        keyword_lower = keyword.lower()
        if not keyword_lower:
            return False
        if keyword_lower in self._haystack_lower:
            return True
        keyword_tokens = tuple(_WORD_PATTERN.findall(keyword_lower))
        if keyword_tokens:
            query_set = set(keyword_tokens)
            return query_set.issubset(self._search_token_set)
        return False

    def merge_tags(self, tags: Iterable[str]) -> None:
        """Merge a collection of tags into the product."""

        initial_tokens = self._search_token_set
        previous_sku = self._sku_lower
        self.tags.update(tag.strip().lower() for tag in tags if tag)
        self._refresh_search_cache()
        if self._owner and self._search_token_set != initial_tokens:
            self._owner.reindex_product(self, previous_sku_lower=previous_sku)

    # ------------------------------------------------------------------
    # Internal helpers for caching normalized data used across searches
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        self._refresh_search_cache()

    def _refresh_search_cache(self) -> None:
        self._sku_lower = self.sku.lower() if self.sku else ""
        self._name_lower = self.name.lower() if self.name else ""
        self._description_lower = self.description.lower() if self.description else None
        tag_blob = " ".join(sorted(tag for tag in self.tags if tag))
        self._search_tokens = _tokenize(self.sku, self.name, self.description, tag_blob)
        self._search_token_set = frozenset(self._search_tokens)
        haystack_parts = [self.sku, self.name, self.description, tag_blob]
        self._haystack_lower = " ".join(part.lower() for part in haystack_parts if part)
        self._name_tokens = frozenset(_tokenize(self.name))

    @property
    def sku_lower(self) -> str:
        return self._sku_lower

    @property
    def name_lower(self) -> str:
        return self._name_lower

    @property
    def description_lower(self) -> str | None:
        return self._description_lower

    @property
    def search_tokens(self) -> tuple[str, ...]:
        return self._search_tokens

    @property
    def search_token_set(self) -> FrozenSet[str]:
        return self._search_token_set

    @property
    def haystack_lower(self) -> str:
        return self._haystack_lower

    @property
    def name_tokens(self) -> frozenset[str]:
        return self._name_tokens

    @property
    def cache_key(self) -> str:
        supplier = self.supplier or ""
        return f"{supplier}|{self.sku_lower}|{self.name_lower}"


@dataclass
class PriceList:
    """Collection of products from a supplier."""

    supplier: str
    products: List[Product] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    _sku_index: Dict[str, List[Product]] = field(init=False, repr=False)
    _token_index: Dict[str, Set[Product]] = field(init=False, repr=False)
    _sorted_offers_cache: Dict[str, List[Product]] = field(init=False, repr=False)
    _sorted_products_cache: List[Product] | None = field(init=False, repr=False, default=None)

    def __post_init__(self) -> None:
        self._sku_index = defaultdict(list)
        self._token_index = defaultdict(set)
        self._sorted_offers_cache = {}
        for product in list(self.products):
            self._attach_product(product)
        self._sorted_products_cache = None

    def _attach_product(self, product: Product) -> None:
        product.supplier = self.supplier
        product._owner = self
        self._sku_index[product.sku_lower].append(product)
        for token in product.search_tokens:
            self._token_index[token].add(product)
        self._sorted_offers_cache.pop(product.sku_lower, None)
        self._sorted_products_cache = None

    def _detach_product(self, product: Product, *, sku_lower_override: str | None = None) -> None:
        sku_key = sku_lower_override if sku_lower_override is not None else product.sku_lower
        bucket = self._sku_index.get(sku_key)
        if bucket and product in bucket:
            bucket.remove(product)
            if not bucket:
                del self._sku_index[sku_key]
        for token, products in list(self._token_index.items()):
            if product in products:
                products.remove(product)
                if not products:
                    del self._token_index[token]
        self._sorted_offers_cache.pop(product.sku_lower, None)
        if sku_lower_override is not None:
            self._sorted_offers_cache.pop(sku_key, None)
        self._sorted_products_cache = None
        product._owner = None

    def reindex_product(self, product: Product, *, previous_sku_lower: str | None = None) -> None:
        """Refresh indexes for an updated product."""

        self._detach_product(product, sku_lower_override=previous_sku_lower)
        self._attach_product(product)

    def add_product(self, product: Product) -> None:
        self.products.append(product)
        self._attach_product(product)

    def by_sku(self) -> Dict[str, Product]:
        """Return a mapping of SKU -> product.

        If multiple products share the same SKU, the cheapest product is used.
        """

        lookup: Dict[str, Product] = {}
        for sku_lower, offers in self._sku_index.items():
            if not offers:
                continue
            sorted_offers = self._sorted_offers_for_sku(sku_lower)
            if sorted_offers:
                lookup[sorted_offers[0].sku] = sorted_offers[0]
        return lookup

    def filter_by_keyword(self, keyword: str) -> List[Product]:
        """Return products that match the given keyword."""

        if not keyword:
            return []
        matches: List[Product] = []
        for product in self.products:
            if product.matches_keyword(keyword):
                matches.append(product)
        return matches

    def sort_products(self) -> None:
        """Sort products in-place by SKU then by price."""

        self.products.sort(key=lambda product: (product.sku, product.price))
        self._sorted_products_cache = list(self.products)

    # ------------------------------------------------------------------
    # Index helpers
    # ------------------------------------------------------------------
    def offers_for_sku(self, sku: str) -> List[Product]:
        """Return cached offers for a SKU sorted by price."""

        sku_lower = sku.lower()
        return list(self._sorted_offers_for_sku(sku_lower))

    def _sorted_offers_for_sku(self, sku_lower: str) -> List[Product]:
        cached = self._sorted_offers_cache.get(sku_lower)
        if cached is not None:
            return cached
        offers = self._sku_index.get(sku_lower)
        if not offers:
            result: List[Product] = []
        else:
            result = sorted(offers, key=lambda product: product.price)
        self._sorted_offers_cache[sku_lower] = result
        return result

    def iter_grouped_offers(self) -> Iterable[tuple[str, List[Product]]]:
        """Iterate over grouped offers keyed by SKU."""

        for sku_lower in self._sku_index.keys():
            offers = self._sorted_offers_for_sku(sku_lower)
            if offers:
                yield offers[0].sku, offers

    def candidates_for_tokens(self, tokens: Iterable[str]) -> Set[Product]:
        """Return all products that contain at least one of the provided tokens."""

        token_set = {token for token in tokens if token}
        if not token_set:
            return set(self.products)
        candidates: Set[Product] = set()
        for token in token_set:
            candidates.update(self._token_index.get(token, set()))
        return candidates

    def all_products_sorted(self) -> List[Product]:
        """Return products sorted by SKU and price with caching."""

        if self._sorted_products_cache is None:
            self.sort_products()
        return list(self._sorted_products_cache or [])
