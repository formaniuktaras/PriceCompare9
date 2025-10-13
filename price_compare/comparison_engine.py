"""Advanced comparison data preparation for the GUI board."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Sequence, Set, Tuple

from .models import PriceList, Product
from .progress import ProgressTracker


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
        self._token_cache: Dict[int, FrozenSet[str]] = {}

    def build(self, tracker: ProgressTracker | None = None) -> List[ComparisonGroup]:
        supplier_pool: List[Tuple[str, Product, FrozenSet[str]]] = []
        supplier_stats: Dict[str, float] = {}
        token_index: Dict[str, List[int]] = defaultdict(list)
        sku_index: Dict[str, List[int]] = defaultdict(list)

        if tracker:
            supplier_product_count = sum(len(price_list.products) for price_list in self.supplier_lists)
            total_steps = supplier_product_count * 2 + len(self.store_products)
            tracker.reset(total_steps)

        for price_list in self.supplier_lists:
            for product in price_list.products:
                if tracker:
                    tracker.wait_if_paused()
                    tracker.raise_if_cancelled()
                match_id = self._match_id(product)
                tokens = self._token_set(product)
                index = len(supplier_pool)
                supplier_pool.append((match_id, product, tokens))
                supplier_stats[match_id] = 0.0
                if product.sku:
                    sku_index[product.sku.lower()].append(index)
                for token in tokens:
                    token_index[token].append(index)
                if tracker:
                    tracker.advance()

        groups: List[ComparisonGroup] = []
        for product in sorted(self.store_products, key=lambda item: item.name.lower()):
            if tracker:
                tracker.wait_if_paused()
                tracker.raise_if_cancelled()
            group_id = self._group_id(product)
            matches: List[SupplierMatch] = []
            store_tokens = self._token_set(product)
            candidate_indices: Set[int] = set()
            if product.sku:
                candidate_indices.update(sku_index.get(product.sku.lower(), []))
            for token in store_tokens:
                candidate_indices.update(token_index.get(token, []))

            for index in candidate_indices:
                match_id, supplier_product, supplier_tokens = supplier_pool[index]
                similarity = self._similarity(
                    product, store_tokens, supplier_product, supplier_tokens
                )
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
            if tracker:
                tracker.advance()

        unmatched_groups = self._build_supplier_only_groups(
            supplier_pool,
            supplier_stats,
            tracker=tracker,
        )
        groups.extend(unmatched_groups)
        if tracker:
            tracker.mark_finished()
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

    def _token_set(self, product: Product) -> FrozenSet[str]:
        key = id(product)
        cached = self._token_cache.get(key)
        if cached is None:
            cached = frozenset(_tokenize_product(product))
            self._token_cache[key] = cached
        return cached

    def _similarity(
        self,
        store_product: Product,
        store_tokens: FrozenSet[str],
        supplier_product: Product,
        supplier_tokens: FrozenSet[str],
    ) -> float:
        if store_product.sku and supplier_product.sku:
            if store_product.sku.lower() == supplier_product.sku.lower():
                return 1.0
        if not store_tokens or not supplier_tokens:
            return 0.0
        intersection = store_tokens & supplier_tokens
        if not intersection:
            return 0.0
        union = store_tokens | supplier_tokens
        if not union:
            return 0.0
        return len(intersection) / len(union)

    def _build_supplier_only_groups(
        self,
        supplier_pool: Sequence[Tuple[str, Product, FrozenSet[str]]],
        stats: Dict[str, float],
        *,
        tracker: ProgressTracker | None = None,
    ) -> List[ComparisonGroup]:
        buckets: Dict[str, List[Tuple[str, Product]]] = defaultdict(list)
        for match_id, product, tokens in supplier_pool:
            if tracker:
                tracker.wait_if_paused()
                tracker.raise_if_cancelled()
            if stats.get(match_id, 0.0) >= self.min_similarity:
                if tracker:
                    tracker.advance()
                continue
            if tokens:
                key = " ".join(sorted(tokens))
            else:
                key = product.name.lower()
            buckets[key].append((match_id, product))
            if tracker:
                tracker.advance()

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

