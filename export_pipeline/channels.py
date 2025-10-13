"""Channel-specific projections built from staging data."""

from __future__ import annotations

import pandas as pd

AVAILABILITY_MAP = {
    "in_stock": "В наявності",
    "preorder": "Під замовлення",
}

PROM_COLUMNS = [
    "ID",
    "Назва товару",
    "Ціна",
    "Стара ціна",
    "Валюта",
    "Стан товару",
    "Наявність",
    "Категорія",
    "Опис",
    "Основне зображення",
    "Додаткові зображення",
    "Артикул",
    "Країна-виробник",
    "Гарантія",
    "Наявність баркоду (ШК)",
    "Бренд",
    "product_url",
]


def _normalize_series(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip()


def staging_to_prom(staging: pd.DataFrame) -> pd.DataFrame:
    """Project staging data into the Prom export schema."""

    if staging is None:
        raise ValueError("staging must be a pandas DataFrame, got None")

    df = staging.copy()

    brand = _normalize_series(df.get("brand", pd.Series(dtype=str)))
    model = _normalize_series(df.get("model", pd.Series(dtype=str)))
    device_type = _normalize_series(df.get("device_type", pd.Series(dtype=str)))

    base_name = (brand + " " + model).str.strip()
    name = base_name
    has_device_type = device_type != ""
    name = name.where(~has_device_type, base_name + " — " + device_type)
    name = name.str.strip()

    availability = df.get("availability", pd.Series(dtype=str)).fillna("")
    availability_ui = availability.map(AVAILABILITY_MAP).fillna(availability)

    prom_df = pd.DataFrame(
        {
            "ID": df.get("id_internal"),
            "Назва товару": name,
            "Ціна": df.get("price"),
            "Стара ціна": df.get("old_price"),
            "Валюта": _normalize_series(df.get("currency", pd.Series(dtype=str))).replace("", "UAH"),
            "Стан товару": "Новий",
            "Наявність": availability_ui,
            "Категорія": df.get("category_norm"),
            "Опис": df.get("description_html_base"),
            "Основне зображення": df.get("images_main_url"),
            "Додаткові зображення": df.get("images_extra_urls"),
            "Артикул": df.get("sku"),
            "Країна-виробник": df.get("country_of_origin"),
            "Гарантія": df.get("warranty_months"),
            "Наявність баркоду (ШК)": df.get("barcode_gtin"),
            "Бренд": df.get("brand"),
            "product_url": df.get("product_url"),
        }
    )

    return prom_df.reindex(columns=PROM_COLUMNS)
