"""Repository for persisting supplier price lists."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

from .models import PriceList, Product


class PriceListRepository:
    """Simple JSON-file based repository for price lists."""

    def __init__(self, data_dir: str | os.PathLike[str] = ".price_compare_data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _file_for_supplier(self, supplier: str) -> Path:
        sanitized = supplier.lower().replace(" ", "_")
        return self.data_dir / f"{sanitized}.json"

    def list_suppliers(self) -> List[str]:
        suppliers: List[str] = []
        for path in self.data_dir.glob("*.json"):
            try:
                with path.open("r", encoding="utf-8") as fp:
                    payload = json.load(fp)
            except (OSError, json.JSONDecodeError):
                continue
            supplier = payload.get("supplier") or path.stem.replace("_", " ")
            suppliers.append(supplier)
        suppliers.sort()
        return suppliers

    def load(self, supplier: str) -> PriceList:
        path = self._file_for_supplier(supplier)
        if not path.exists():
            raise FileNotFoundError(f"No price list stored for supplier '{supplier}'.")

        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

        products = [
            Product(
                sku=item["sku"],
                name=item["name"],
                price=item["price"],
                currency=item.get("currency", "USD"),
                description=item.get("description"),
                supplier=supplier,
                tags=set(item.get("tags", [])),
                extra=item.get("extra", {}),
            )
            for item in payload.get("products", [])
        ]
        return PriceList(supplier=supplier, products=products, metadata=payload.get("metadata", {}))

    def load_all(self) -> Dict[str, PriceList]:
        return {supplier: self.load(supplier) for supplier in self.list_suppliers()}

    def save(self, price_list: PriceList) -> None:
        path = self._file_for_supplier(price_list.supplier)
        payload = {
            "supplier": price_list.supplier,
            "metadata": price_list.metadata,
            "products": [
                {
                    "sku": product.sku,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "description": product.description,
                    "tags": sorted(product.tags),
                    "extra": product.extra,
                }
                for product in price_list.products
            ],
        }
        with path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, ensure_ascii=False)

    def delete(self, supplier: str) -> None:
        path = self._file_for_supplier(supplier)
        if path.exists():
            path.unlink()

    def clear(self) -> None:
        for supplier in self.list_suppliers():
            self.delete(supplier)
