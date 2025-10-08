"""Advanced comparison data preparation for the GUI board."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .comparator import PriceComparator
from .models import PriceList, Product


def _stable_hash(*parts: str) -> str:
    payload = "||".join(part.lower().strip() for part in parts if part)
    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()
    return digest[:16]


def _tokenize_product(product: Product) -> List[str]:
    tokens: List[str] = []
    for source in (product.name, product.sku):
        if not source:
            continue
        tokens.extend(token for token in source.lower().split() if token)
    tokens.extend(tag.lower() for tag in product.tags if tag)
    seen: Dict[str, None] = {}
    unique_tokens: List[str] = []
    for token in tokens:
        if token not in seen:
            seen[token] = None
            unique_tokens.append(token)
    return unique_tokens


def _comparable_text(product: Product) -> str:
    return " ".join(_tokenize_product(product))


@dataclass
class SupplierMatch:
    match_id: str
    product: Product
    similarity: float


@dataclass
class ComparisonGroup:
    group_id: str
    store_product: Product | None
    supplier_matches: List[SupplierMatch]

    @property
    def has_supplier_matches(self) -> bool:
        return bool(self.supplier_matches)


class ComparisonBuilder:
    """Build comparison groups for the advanced comparison UI."""

    def __init__(
        self,
        store_products: Sequence[Product],
        supplier_lists: Sequence[PriceList],
        *,
        min_similarity: float = 0.15,
        max_matches: int = 8,
    ) -> None:
        self.store_products = list(store_products)
        self.supplier_lists = list(supplier_lists)
        self.min_similarity = min_similarity
        self.max_matches = max_matches
        self._comparator = PriceComparator([])

    def build(self) -> List[ComparisonGroup]:
        supplier_pool: List[Tuple[str, Product]] = []
        supplier_stats: Dict[str, float] = {}
        for price_list in self.supplier_lists:
            for product in price_list.products:
                match_id = self._match_id(product)
                supplier_pool.append((match_id, product))
                supplier_stats[match_id] = 0.0

        groups: List[ComparisonGroup] = []
        for product in sorted(self.store_products, key=lambda item: item.name.lower()):
            group_id = self._group_id(product)
            matches: List[SupplierMatch] = []
            for match_id, supplier_product in supplier_pool:
                similarity = self._product_similarity(product, supplier_product)
                supplier_stats[match_id] = max(supplier_stats[match_id], similarity)
                if similarity >= self.min_similarity:
                    matches.append(
                        SupplierMatch(
                            match_id=match_id,
                            product=supplier_product,
                            similarity=similarity,
                        )
                    )

            matches.sort(key=lambda match: (-match.similarity, match.product.price))
            if self.max_matches:
                matches = matches[: self.max_matches]
            groups.append(
                ComparisonGroup(
                    group_id=group_id,
                    store_product=product,
                    supplier_matches=matches,
                )
            )

        unmatched_groups = self._build_supplier_only_groups(supplier_pool, supplier_stats)
        groups.extend(unmatched_groups)
        return groups

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _match_id(self, product: Product) -> str:
        supplier = product.supplier or "unknown"
        return f"match::{_stable_hash(supplier, product.sku or '', product.name or '')}"

    def _group_id(self, product: Product) -> str:
        base = product.sku or product.name or "product"
        return f"store::{_stable_hash(base)}"

    def _product_similarity(self, store_product: Product, supplier_product: Product) -> float:
        if store_product.sku and supplier_product.sku:
            if store_product.sku.lower() == supplier_product.sku.lower():
                return 1.0
        left = _comparable_text(store_product)
        right = _comparable_text(supplier_product)
        if not left or not right:
            return 0.0
        return self._comparator._name_similarity(left, right)

    def _build_supplier_only_groups(
        self,
        supplier_pool: Sequence[Tuple[str, Product]],
        stats: Dict[str, float],
    ) -> List[ComparisonGroup]:
        buckets: Dict[str, List[Tuple[str, Product]]] = defaultdict(list)
        for match_id, product in supplier_pool:
            if stats.get(match_id, 0.0) >= self.min_similarity:
                continue
            key = " ".join(_tokenize_product(product)) or product.name.lower()
            buckets[key].append((match_id, product))

        groups: List[ComparisonGroup] = []
        for key, items in buckets.items():
            if len(items) < 2:
                continue
            group_id = f"supplier::{_stable_hash(key)}"
            matches = [
                SupplierMatch(match_id=match_id, product=product, similarity=1.0)
                for match_id, product in items
            ]
            matches.sort(key=lambda match: (match.product.name.lower(), match.product.price))
            groups.append(
                ComparisonGroup(group_id=group_id, store_product=None, supplier_matches=matches)
            )
        groups.sort(key=lambda group: group.supplier_matches[0].product.name.lower())
        return groups

