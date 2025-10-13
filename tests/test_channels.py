import pytest

pd = pytest.importorskip("pandas")

from export_pipeline.channels import staging_to_prom


def test_staging_to_prom_builds_expected_columns():
    staging = pd.DataFrame(
        {
            "id_internal": ["1", "2"],
            "brand": ["Apple", pd.NA],
            "model": ["iPhone X", "Galaxy"],
            "device_type": ["Смартфон", ""],
            "price": [1000, 2000],
            "old_price": [1100, None],
            "currency": ["usd", None],
            "availability": ["in_stock", "preorder"],
            "category_norm": ["phones", "phones"],
            "description_html_base": ["<p>desc</p>", "<p>desc2</p>"],
            "images_main_url": ["http://image/1.jpg", "http://image/2.jpg"],
            "images_extra_urls": ["http://image/3.jpg", ""],
            "sku": ["SKU1", "SKU2"],
            "country_of_origin": ["UA", "CN"],
            "warranty_months": [12, 24],
            "barcode_gtin": ["1234567890123", ""],
            "product_url": ["http://example.com/1", "http://example.com/2"],
        }
    )

    prom = staging_to_prom(staging)

    assert list(prom.columns) == [
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
    assert prom.loc[0, "Назва товару"] == "Apple iPhone X — Смартфон"
    assert prom.loc[0, "Валюта"] == "usd"
    assert prom.loc[0, "Наявність"] == "В наявності"
    assert prom.loc[1, "Назва товару"] == "Galaxy"
    assert prom.loc[1, "Валюта"] == "UAH"
    assert prom.loc[1, "Наявність"] == "Під замовлення"
    assert (prom["Стан товару"] == "Новий").all()
