"""Command line interface for the price comparison toolkit."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Sequence

from .comparator import PriceComparator
from .io import MissingRequiredColumnsError, PriceListExporter, PriceListImporter
from .repository import PriceListRepository
from .search import ProductSearch
from .tagging import Tagger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Price list management and comparison tool")
    parser.add_argument(
        "--data-dir",
        default=".price_compare_data",
        help="Directory where imported price lists are stored (default: %(default)s)",
    )
    parser.add_argument(
        "--tags-config",
        default=None,
        help="Optional JSON file with custom tagging rules",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Import a price list")
    import_parser.add_argument("supplier", help="Supplier name")
    import_parser.add_argument("path", help="Path to CSV or JSON price list")
    import_parser.add_argument(
        "--mapping",
        default=None,
        help=(
            "Optional column mapping as JSON string or path to a JSON file. "
            "Example: '{\"sku\": [\"код товару\", \"артикул\"], \"price\": \"вартість\"}'"
        ),
    )

    subparsers.add_parser("suppliers", help="List registered suppliers")

    list_parser = subparsers.add_parser("products", help="List products for a supplier")
    list_parser.add_argument("supplier", help="Supplier name")
    list_parser.add_argument("--limit", type=int, default=50, help="Maximum number of products to show")

    search_parser = subparsers.add_parser("search", help="Search products across suppliers")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--supplier", help="Filter to supplier", default=None)
    search_parser.add_argument("--fuzzy", action="store_true", help="Enable fuzzy search")
    search_parser.add_argument("--threshold", type=float, default=0.65, help="Fuzzy score threshold")
    search_parser.add_argument("--limit", type=int, default=20, help="Maximum number of results")

    compare_parser = subparsers.add_parser("compare", help="Compare offers for a SKU")
    compare_parser.add_argument("sku", help="SKU to compare")

    compare_name_parser = subparsers.add_parser("compare-name", help="Compare offers by product name")
    compare_name_parser.add_argument("name", help="Name to match")
    compare_name_parser.add_argument("--threshold", type=float, default=0.75, help="Match threshold")

    best_parser = subparsers.add_parser("best", help="List best offers for all products")
    best_parser.add_argument("--limit", type=int, default=20, help="Number of products to display")

    export_parser = subparsers.add_parser("export", help="Export search results to a file")
    export_parser.add_argument("query", help="Query to search for")
    export_parser.add_argument("output", help="Output file path (CSV or JSON)")
    export_parser.add_argument("--supplier", help="Restrict to supplier", default=None)
    export_parser.add_argument("--fuzzy", action="store_true", help="Use fuzzy search")
    export_parser.add_argument("--threshold", type=float, default=0.65, help="Fuzzy threshold")
    export_parser.add_argument("--limit", type=int, default=100, help="Maximum number of results")

    subparsers.add_parser("clear", help="Remove all stored price lists")

    return parser


def _load_price_lists(repository: PriceListRepository) -> List:
    return [repository.load(supplier) for supplier in repository.list_suppliers()]


def _make_tagger(tags_config: str | None) -> Tagger:
    if tags_config:
        return Tagger.from_json(tags_config)
    return Tagger()


def _load_column_mapping_arg(
    mapping_argument: str | None,
) -> dict[str, str | Sequence[str]] | None:
    if not mapping_argument:
        return None

    try:
        payload = json.loads(mapping_argument)
    except json.JSONDecodeError:
        path = Path(mapping_argument)
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

    if not isinstance(payload, dict):
        raise ValueError("Column mapping must be a JSON object with field definitions.")

    allowed_fields = {"sku", "name", "price", "currency", "description", "tags"}
    mapping: dict[str, str | Sequence[str]] = {}
    for key, value in payload.items():
        field = str(key).strip().lower()
        if field not in allowed_fields:
            raise ValueError(
                f"Unsupported column mapping key '{key}'. Allowed keys: {sorted(allowed_fields)}."
            )

        if isinstance(value, str):
            mapping[field] = value
        elif isinstance(value, (list, tuple, set)):
            mapping[field] = [str(item) for item in value]
        else:
            raise ValueError(
                f"Mapping for '{field}' must be a string or list of strings, got {type(value).__name__}."
            )

    return mapping or None


def _prompt_manual_column_mapping(
    missing: Sequence[str],
    headers: Sequence[str],
    resolved: dict[str, str],
    current_mapping: dict[str, str | Sequence[str]] | None,
) -> dict[str, str | Sequence[str]] | None:
    if not sys.stdin.isatty():
        print("Неможливо виконати ручне зіставлення у неінтерактивному режимі.")
        return None

    available_headers = [header for header in headers if header]
    if not available_headers:
        print("У файлі відсутні заголовки, ручне зіставлення неможливе.")
        return None

    print("Автоматичне зіставлення не знайшло всі обов'язкові поля.")
    if resolved:
        resolved_pairs = ", ".join(f"{field} → {column}" for field, column in resolved.items())
        print(f"Вже знайдено: {resolved_pairs}")
    print("Доступні стовпці:")
    for index, header in enumerate(available_headers, start=1):
        print(f"  {index}. {header}")
    print("Введіть номер або точну назву стовпця. Напишіть 'exit' щоб скасувати імпорт.")

    mapping: dict[str, str | Sequence[str]] = {}
    if current_mapping:
        mapping.update(current_mapping)

    for field in missing:
        while True:
            response = input(f"Стовпець для '{field}': ").strip()
            if not response:
                print("Це поле обов'язкове, введіть значення або 'exit'.")
                continue
            if response.lower() in {"exit", "quit"}:
                return None

            selected: str | None = None
            if response.isdigit():
                index = int(response)
                if 1 <= index <= len(available_headers):
                    selected = available_headers[index - 1]
                else:
                    print("Невірний номер стовпця.")
                    continue
            else:
                for header in available_headers:
                    if header.strip().lower() == response.lower():
                        selected = header
                        break
                if not selected:
                    print("Стовпець не знайдено, спробуйте ще раз.")
                    continue

            mapping[field] = selected
            break

    return mapping


def cmd_import(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    importer = PriceListImporter()
    try:
        column_mapping = _load_column_mapping_arg(args.mapping)
    except (OSError, ValueError) as exc:
        print(f"Failed to load column mapping: {exc}")
        return
    tagger = _make_tagger(args.tags_config)
    while True:
        try:
            price_list = importer.load(
                args.path, supplier=args.supplier, column_mapping=column_mapping
            )
            break
        except MissingRequiredColumnsError as exc:
            manual_mapping = _prompt_manual_column_mapping(
                missing=exc.missing,
                headers=exc.headers,
                resolved=exc.resolved,
                current_mapping=column_mapping,
            )
            if manual_mapping is None:
                print(str(exc))
                return
            column_mapping = manual_mapping
        except ValueError as exc:
            print(str(exc))
            return
    tagger.apply(price_list.products)
    repository.save(price_list)
    print(f"Imported {len(price_list.products)} products for supplier '{price_list.supplier}'.")


def cmd_suppliers(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    suppliers = repository.list_suppliers()
    if not suppliers:
        print("No suppliers have been imported yet.")
        return
    for supplier in suppliers:
        price_list = repository.load(supplier)
        print(f"- {supplier} ({len(price_list.products)} products)")


def cmd_products(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    try:
        price_list = repository.load(args.supplier)
    except FileNotFoundError as exc:
        print(str(exc))
        return

    print(f"Products for supplier '{price_list.supplier}':")
    for product in price_list.products[: args.limit]:
        tags = f" [{', '.join(sorted(product.tags))}]" if product.tags else ""
        print(f"- {product.sku}: {product.name} — {product.price:.2f} {product.currency}{tags}")


def cmd_search(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    price_lists = _load_price_lists(repository)
    search = ProductSearch(price_lists)
    results = search.search(
        args.query,
        supplier=args.supplier,
        fuzzy=args.fuzzy,
        limit=args.limit,
        threshold=args.threshold,
    )

    if not results:
        print("No products matched your query.")
        return

    for result in results:
        product = result.product
        score = f" (score {result.score:.2f})" if args.fuzzy else ""
        tags = f" [{', '.join(sorted(product.tags))}]" if product.tags else ""
        print(
            f"- {product.sku} | {product.name} | {product.price:.2f} {product.currency} | "
            f"{product.supplier}{tags}{score}"
        )


def cmd_compare(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    price_lists = _load_price_lists(repository)
    comparator = PriceComparator(price_lists)
    entry = comparator.compare_by_sku(args.sku)
    if not entry:
        print(f"No offers found for SKU '{args.sku}'.")
        return

    print(f"Offers for {entry.sku} — {entry.name}:")
    for product in entry.offers:
        tags = f" [{', '.join(sorted(product.tags))}]" if product.tags else ""
        print(f"- {product.supplier}: {product.price:.2f} {product.currency}{tags}")


def cmd_compare_name(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    price_lists = _load_price_lists(repository)
    comparator = PriceComparator(price_lists)
    entries = comparator.compare_by_name(args.name, threshold=args.threshold)
    if not entries:
        print("No similar products found across suppliers.")
        return

    for entry in entries:
        print(f"SKU {entry.sku} — {entry.name}")
        for offer in entry.offers:
            tags = f" [{', '.join(sorted(offer.tags))}]" if offer.tags else ""
            print(f"  - {offer.supplier}: {offer.price:.2f} {offer.currency}{tags}")


def cmd_best(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    price_lists = _load_price_lists(repository)
    comparator = PriceComparator(price_lists)
    entries = comparator.best_offers()
    if not entries:
        print("No products available. Import price lists first.")
        return

    for entry in entries[: args.limit]:
        best_offer = entry.best_offer
        if best_offer is None:
            continue
        print(
            f"{entry.sku} — {entry.name}: {best_offer.price:.2f} {best_offer.currency} from {best_offer.supplier}"
        )


def cmd_export(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    price_lists = _load_price_lists(repository)
    search = ProductSearch(price_lists)
    results = search.search(
        args.query,
        supplier=args.supplier,
        fuzzy=args.fuzzy,
        limit=args.limit,
        threshold=args.threshold,
    )

    if not results:
        print("No products matched your query; nothing exported.")
        return

    exporter = PriceListExporter()
    exporter.export([result.product for result in results], args.output)
    print(f"Exported {len(results)} products to {args.output}.")


def cmd_clear(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    repository.clear()
    print("Cleared all stored price lists.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)

    command_map = {
        "import": cmd_import,
        "suppliers": cmd_suppliers,
        "products": cmd_products,
        "search": cmd_search,
        "compare": cmd_compare,
        "compare-name": cmd_compare_name,
        "best": cmd_best,
        "export": cmd_export,
        "clear": cmd_clear,
    }

    handler = command_map[args.command]
    handler(args)


if __name__ == "__main__":
    main()
