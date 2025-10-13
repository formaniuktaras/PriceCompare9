"""Writers for exporting channel data to various formats."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

LOGGER = logging.getLogger(__name__)
XML_ESCAPE_MAP = {'"': "&quot;", "'": "&apos;"}


def to_csv(df: pd.DataFrame, out_path: str | Path) -> None:
    """Write the DataFrame to CSV."""

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def to_xlsx(df: pd.DataFrame, out_path: str | Path) -> None:
    """Write the DataFrame to XLSX using openpyxl."""

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, engine="openpyxl")


def _split_images(main: str, extra: str | float | None) -> list[str]:
    images: list[str] = [main]
    if isinstance(extra, str):
        parts = re.split(r"[,|]", extra)
        images.extend(part.strip() for part in parts if part.strip())
    return images[:10]


def _availability_flags(status: str) -> tuple[str, bool]:
    if status == "В наявності":
        return "склад", True
    if status == "Під замовлення":
        return "", False
    return "false", False


def _safe_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def _should_skip(price_value: object) -> bool:
    if price_value is None or (isinstance(price_value, float) and pd.isna(price_value)):
        return True
    try:
        return float(str(price_value).replace(",", ".")) <= 0
    except (TypeError, ValueError):
        return True


def _to_price_number(price_value: object) -> float:
    return float(str(price_value).replace(",", "."))


def to_priceua_xml(
    prom_df: pd.DataFrame,
    out_path: str | Path,
    *,
    selling_type: str = "r",
) -> None:
    """Write a Price.ua compatible XML export from the Prom dataframe."""

    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<shop>", "  <items>"]

    for _, row in prom_df.iterrows():
        price = row.get("Ціна")
        if _should_skip(price):
            LOGGER.warning("Skipping item %s due to missing or invalid price", row.get("ID"))
            continue

        availability_text = _safe_text(row.get("Наявність"))
        available_flag, in_stock_flag = _availability_flags(availability_text)

        main_image = row.get("Основне зображення")
        if not isinstance(main_image, str) or not main_image.strip():
            LOGGER.warning(
                "Item %s missing main image; export will not include image tags",
                row.get("ID"),
            )
            images: list[str] = []
        else:
            images = _split_images(main_image.strip(), row.get("Додаткові зображення"))

        item_id = escape(_safe_text(row.get("ID")), XML_ESCAPE_MAP)
        item_selling_type = escape(str(selling_type), XML_ESCAPE_MAP)
        lines.append(f"    <item id=\"{item_id}\" selling_type=\"{item_selling_type}\">")

        for image in images:
            lines.append(f"      <image>{escape(image, XML_ESCAPE_MAP)}</image>")

        lines.append(f"      <available>{escape(available_flag, XML_ESCAPE_MAP)}</available>")
        lines.append(f"      <in_stock>{'true' if in_stock_flag else 'false'}</in_stock>")

        price_number = _to_price_number(price)
        price_str = f"{price_number:.2f}"
        lines.append(f"      <priceuah>{escape(price_str, XML_ESCAPE_MAP)}</priceuah>")

        lines.append(
            f"      <name>{escape(_safe_text(row.get('Назва товару')), XML_ESCAPE_MAP)}</name>"
        )
        lines.append(
            f"      <vendor>{escape(_safe_text(row.get('Бренд')), XML_ESCAPE_MAP)}</vendor>"
        )
        lines.append(
            f"      <model>{escape(_safe_text(row.get('Артикул')), XML_ESCAPE_MAP)}</model>"
        )
        lines.append(
            f"      <categoryId>{escape(_safe_text(row.get('Категорія')), XML_ESCAPE_MAP)}</categoryId>"
        )
        lines.append("      <currencyId>UAH</currencyId>")
        lines.append(
            f"      <vendorCode>{escape(_safe_text(row.get('Артикул')), XML_ESCAPE_MAP)}</vendorCode>"
        )
        lines.append(
            f"      <description>{escape(_safe_text(row.get('Опис')), XML_ESCAPE_MAP)}</description>"
        )
        lines.append("    </item>")

    lines.append("  </items>")
    lines.append("</shop>")

    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
