"""Repository for persisting supplier price lists."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

from .models import PriceList, Product


class PriceListRepository:
    """Simple JSON-file based repository for price lists."""

    def __init__(self, data_dir: str | os.PathLike[str] = ".price_compare_data") -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, tuple[float, PriceList]] = {}

    def _file_for_supplier(self, supplier: str) -> Path:
        sanitized = supplier.lower().replace(" ", "_")
        return self.data_dir / f"{sanitized}.json"

    def _load_payload(self, path: Path) -> dict | None:
        try:
            with path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None

        return payload

    def _is_price_list_payload(self, payload: dict) -> bool:
        products = payload.get("products")
        return isinstance(products, list)

    def _deserialize_price_list(self, supplier: str, payload: dict) -> PriceList:
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

    def list_suppliers(self) -> List[str]:
        suppliers: List[str] = []
        for path in self.data_dir.glob("*.json"):
            payload = self._load_payload(path)
            if payload is None:
                continue

            if not self._is_price_list_payload(payload):
                continue

            supplier = payload.get("supplier") or path.stem.replace("_", " ")
            expected_name = self._file_for_supplier(supplier).name
            if path.name != expected_name:
                continue

            suppliers.append(supplier)
        suppliers.sort()
        return suppliers

    def load(self, supplier: str) -> PriceList:
        path = self._file_for_supplier(supplier)
        if not path.exists():
            raise FileNotFoundError(f"No price list stored for supplier '{supplier}'.")

        mtime = path.stat().st_mtime
        cached = self._cache.get(supplier)
        if cached and cached[0] == mtime:
            return cached[1]

        payload = self._load_payload(path)
        if payload is None:
            raise FileNotFoundError(f"No price list stored for supplier '{supplier}'.")

        price_list = self._deserialize_price_list(supplier, payload)
        self._cache[supplier] = (mtime, price_list)
        return price_list

    def load_all(self) -> Dict[str, PriceList]:
        suppliers = self.list_suppliers()
        return {supplier: self.load(supplier) for supplier in suppliers}

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
        mtime = path.stat().st_mtime
        self._cache[price_list.supplier] = (mtime, price_list)

    def delete(self, supplier: str) -> None:
        path = self._file_for_supplier(supplier)
        if path.exists():
            path.unlink()
        self._cache.pop(supplier, None)

    def rename(self, old_supplier: str, new_supplier: str) -> None:
        old_path = self._file_for_supplier(old_supplier)
        if not old_path.exists():
            raise FileNotFoundError(
                f"No price list stored for supplier '{old_supplier}'."
            )
        new_path = self._file_for_supplier(new_supplier)
        if new_path.exists():
            raise FileExistsError(
                f"A price list already exists for supplier '{new_supplier}'."
            )
        old_path.rename(new_path)
        payload = self._load_payload(new_path)
        if payload and self._is_price_list_payload(payload):
            payload["supplier"] = new_supplier
            with new_path.open("w", encoding="utf-8") as fp:
                json.dump(payload, fp, indent=2, ensure_ascii=False)
        self._cache.pop(old_supplier, None)
        self._cache.pop(new_supplier, None)

    def clear(self) -> None:
        for supplier in self.list_suppliers():
            self.delete(supplier)
        self._cache.clear()
