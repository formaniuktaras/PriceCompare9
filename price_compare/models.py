"""Domain models for price comparison."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Set


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

    def matches_keyword(self, keyword: str) -> bool:
        """Check if the product matches the supplied keyword."""

        keyword_lower = keyword.lower()
        haystacks = filter(
            None,
            [
                self.sku,
                self.name,
                self.description,
                " ".join(sorted(self.tags)) if self.tags else None,
            ],
        )
        return any(keyword_lower in field.lower() for field in haystacks)

    def merge_tags(self, tags: Iterable[str]) -> None:
        """Merge a collection of tags into the product."""

        self.tags.update(tag.strip().lower() for tag in tags if tag)


@dataclass
class PriceList:
    """Collection of products from a supplier."""

    supplier: str
    products: List[Product] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def add_product(self, product: Product) -> None:
        product.supplier = self.supplier
        self.products.append(product)

    def by_sku(self) -> Dict[str, Product]:
        """Return a mapping of SKU -> product.

        If multiple products share the same SKU, the cheapest product is used.
        """

        lookup: Dict[str, Product] = {}
        for product in self.products:
            if product.sku not in lookup or product.price < lookup[product.sku].price:
                lookup[product.sku] = product
        return lookup

    def filter_by_keyword(self, keyword: str) -> List[Product]:
        """Return products that match the given keyword."""

        return [product for product in self.products if product.matches_keyword(keyword)]

    def sort_products(self) -> None:
        """Sort products in-place by SKU then by price."""

        self.products.sort(key=lambda product: (product.sku, product.price))
