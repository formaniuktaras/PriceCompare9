"""Import and export utilities for price lists."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Sequence

from .models import PriceList, Product

SUPPORTED_IMPORT_FORMATS = {".csv", ".json"}
SUPPORTED_EXPORT_FORMATS = {".csv", ".json"}


class PriceListImporter:
    """Load price lists from supported file formats."""

    def __init__(self, default_currency: str = "USD") -> None:
        self.default_currency = default_currency

    def load(self, path: str | Path, supplier: str | None = None) -> PriceList:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_IMPORT_FORMATS:
            raise ValueError(f"Unsupported import format '{suffix}'.")

        if suffix == ".csv":
            products = list(self._load_csv(path, supplier))
        else:
            products = list(self._load_json(path, supplier))

        supplier_name = supplier or path.stem
        price_list = PriceList(supplier=supplier_name, products=products)
        price_list.sort_products()
        return price_list

    def _load_csv(self, path: Path, supplier: str | None) -> Iterable[Product]:
        with path.open("r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            required_fields = {"sku", "name", "price"}
            missing = required_fields - set(field.lower() for field in reader.fieldnames or [])
            if missing:
                raise ValueError(
                    f"Missing required columns {sorted(missing)} in CSV file '{path}'."
                )

            for row in reader:
                sku = (row.get("sku") or row.get("SKU") or "").strip()
                name = (row.get("name") or row.get("Name") or "").strip()
                price_str = row.get("price") or row.get("Price")
                currency = (row.get("currency") or row.get("Currency") or self.default_currency).strip()
                description = (row.get("description") or row.get("Description") or "").strip() or None
                tags_raw = row.get("tags") or row.get("Tags") or ""
                extra = {
                    key: value
                    for key, value in row.items()
                    if key not in {"sku", "name", "price", "currency", "description", "tags"}
                    and value
                }

                if not sku or not name or price_str is None:
                    continue

                try:
                    price = float(str(price_str).replace(",", "."))
                except ValueError as exc:
                    raise ValueError(f"Invalid price '{price_str}' for SKU '{sku}'.") from exc

                tags = {tag.strip().lower() for tag in tags_raw.split(";") if tag.strip()}

                yield Product(
                    sku=sku,
                    name=name,
                    price=price,
                    currency=currency,
                    description=description,
                    supplier=supplier,
                    tags=tags,
                    extra=extra,
                )

    def _load_json(self, path: Path, supplier: str | None) -> Iterable[Product]:
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

        products_data = payload.get("products")
        if not isinstance(products_data, list):
            raise ValueError("JSON payload must contain a 'products' list.")

        for item in products_data:
            sku = str(item.get("sku", "")).strip()
            name = str(item.get("name", "")).strip()
            price_raw = item.get("price")
            if not sku or not name or price_raw is None:
                continue

            try:
                price = float(price_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid price '{price_raw}' for SKU '{sku}'.") from exc

            yield Product(
                sku=sku,
                name=name,
                price=price,
                currency=str(item.get("currency", self.default_currency)),
                description=item.get("description"),
                supplier=supplier,
                tags={tag.strip().lower() for tag in item.get("tags", []) if tag},
                extra={key: value for key, value in item.get("extra", {}).items()},
            )


class PriceListExporter:
    """Export price list or product collections into supported formats."""

    def export(self, products: Sequence[Product], path: str | Path) -> None:
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_EXPORT_FORMATS:
            raise ValueError(f"Unsupported export format '{suffix}'.")

        if suffix == ".csv":
            self._export_csv(products, path)
        else:
            self._export_json(products, path)

    def _export_csv(self, products: Sequence[Product], path: Path) -> None:
        fieldnames = ["sku", "name", "price", "currency", "description", "tags", "supplier"]
        extra_fields = sorted({key for product in products for key in product.extra})
        fieldnames.extend(extra_fields)

        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            for product in products:
                row = {
                    "sku": product.sku,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "description": product.description or "",
                    "tags": ";".join(sorted(product.tags)),
                    "supplier": product.supplier or "",
                }
                for key in extra_fields:
                    row[key] = product.extra.get(key, "")
                writer.writerow(row)

    def _export_json(self, products: Sequence[Product], path: Path) -> None:
        payload = {
            "products": [
                {
                    "sku": product.sku,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "description": product.description,
                    "tags": sorted(product.tags),
                    "supplier": product.supplier,
                    "extra": product.extra,
                }
                for product in products
            ]
        }
        with path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, ensure_ascii=False)
