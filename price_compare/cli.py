"""Command line interface for the price comparison toolkit."""

from __future__ import annotations

import argparse
from typing import List, Sequence

from .comparator import PriceComparator
from .io import PriceListExporter, PriceListImporter
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


def cmd_import(args: argparse.Namespace) -> None:
    repository = PriceListRepository(args.data_dir)
    importer = PriceListImporter()
    tagger = _make_tagger(args.tags_config)
    price_list = importer.load(args.path, supplier=args.supplier)
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
