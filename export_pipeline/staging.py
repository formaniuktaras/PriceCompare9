"""Utilities for building normalized export staging data."""

from __future__ import annotations

import pandas as pd

STAGING_COLUMNS = [
    "id_internal",
    "sku",
    "title_base",
    "brand",
    "model",
    "device_type",
    "category_norm",
    "price",
    "old_price",
    "currency",
    "stock_qty",
    "availability",
    "condition",
    "warranty_months",
    "barcode_gtin",
    "mpn",
    "color",
    "material",
    "compatibility",
    "weight_g",
    "size_mm_l",
    "size_mm_w",
    "size_mm_h",
    "country_of_origin",
    "description_html_base",
    "images_main_url",
    "images_extra_urls",
    "product_url",
    "tags",
    "updated_at",
    "channel_exclude",
]


def build_staging_df(raw_df: pd.DataFrame) -> pd.DataFrame:
    """Return a DataFrame containing the normalized staging view."""

    if raw_df is None:
        raise ValueError("raw_df must be a pandas DataFrame, got None")

    staging_df = raw_df.copy()
    missing_columns = [col for col in STAGING_COLUMNS if col not in staging_df.columns]
    for column in missing_columns:
        staging_df[column] = pd.NA

    return staging_df.reindex(columns=STAGING_COLUMNS)
