"""Automatic tagging of products based on keyword rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from .models import Product


@dataclass(frozen=True)
class TagRule:
    """A rule that assigns a tag when one of the keywords is present."""

    tag: str
    keywords: Sequence[str]

    def matches(self, text: str) -> bool:
        lowered = text.lower()
        return any(keyword.lower() in lowered for keyword in self.keywords)


DEFAULT_TAG_RULES: List[TagRule] = [
    TagRule(tag="laptop", keywords=["laptop", "notebook", "ultrabook"]),
    TagRule(tag="smartphone", keywords=["smartphone", "phone", "iphone", "android"]),
    TagRule(tag="display", keywords=["monitor", "display", "screen"]),
    TagRule(tag="storage", keywords=["ssd", "hard drive", "hdd", "nvme", "flash"]),
    TagRule(tag="network", keywords=["router", "switch", "ethernet", "wifi", "wireless"]),
    TagRule(tag="printer", keywords=["printer", "toner", "inkjet", "laserjet"]),
]


class Tagger:
    """Assign tags to products based on keyword rules."""

    def __init__(self, rules: Sequence[TagRule] | None = None) -> None:
        self.rules = list(rules or DEFAULT_TAG_RULES)

    @classmethod
    def from_json(cls, path: str | Path) -> "Tagger":
        path = Path(path)
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

        rules = [
            TagRule(tag=item["tag"], keywords=item.get("keywords", []))
            for item in payload.get("rules", [])
            if item.get("tag")
        ]
        return cls(rules=rules)

    def apply(self, products: Iterable[Product]) -> None:
        for product in products:
            self.apply_to_product(product)

    def apply_to_product(self, product: Product) -> None:
        haystack = " ".join(filter(None, [product.name, product.description or ""]))
        for rule in self.rules:
            if rule.matches(haystack):
                product.tags.add(rule.tag)
