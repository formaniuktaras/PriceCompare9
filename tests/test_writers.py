from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

pd = pytest.importorskip("pandas")

from export_pipeline.writers import to_priceua_xml


def test_to_priceua_xml_builds_valid_priceua(tmp_path, caplog):
    caplog.set_level('WARNING')
    prom_df = pd.DataFrame(
        {
            "ID": ["SKU-1", "SKU-2", "SKU-3", "SKU-4"],
            "Назва товару": ["Ноутбук & Pro", "Мишка", "Пристрій", "Гаджет"],
            "Ціна": ["123.45", 200.0, 0, 50],
            "Стара ціна": [None, None, None, None],
            "Валюта": ["UAH", "UAH", "UAH", "UAH"],
            "Стан товару": ["Новий", "Новий", "Новий", "Новий"],
            "Наявність": ["В наявності", "Немає в наявності", "В наявності", "Під замовлення"],
            "Категорія": ["cat1", "cat2", "cat3", "cat4"],
            "Опис": ["<p>Опис & тест</p>", "desc", "desc", "desc"],
            "Основне зображення": [
                "http://img/main1.jpg",
                "",
                "http://img/main3.jpg",
                "http://img/main4.jpg",
            ],
            "Додаткові зображення": [
                "http://img/extra1.jpg,http://img/extra2.jpg,http://img/extra3.jpg",
                "http://img/extra4.jpg|http://img/extra5.jpg",
                ",".join(f"http://img/{i}.jpg" for i in range(20)),
                ",".join(f"http://img/extra{i}.jpg" for i in range(25)),
            ],
            "Артикул": ["A1", "A2", "A3", "A4"],
            "Країна-виробник": ["UA", "CN", "US", "UA"],
            "Гарантія": [12, 24, 36, 12],
            "Наявність баркоду (ШК)": ["111", "222", "333", "444"],
            "Бренд": ["Brand", "Brand", "Brand", "Brand"],
            "product_url": [
                "http://example.com/1",
                "http://example.com/2",
                "http://example.com/3",
                "http://example.com/4",
            ],
        }
    )

    output_file = tmp_path / "priceua.xml"
    to_priceua_xml(prom_df, output_file)

    tree = ET.parse(output_file)
    root = tree.getroot()

    assert root.tag == "shop"
    items = root.find("items")
    assert items is not None
    item_nodes = items.findall("item")

    # Third row skipped because price 0
    assert len(item_nodes) == 3

    first_item = item_nodes[0]
    assert first_item.attrib["id"] == "SKU-1"
    assert first_item.attrib["selling_type"] == "r"
    images = first_item.findall("image")
    assert len(images) == 4  # main + 3 extras
    available = first_item.findtext("available")
    assert available == "склад"
    assert first_item.findtext("in_stock") == "true"
    assert first_item.findtext("priceuah") == "123.45"
    assert "&amp;" in first_item.findtext("name")

    second_item = item_nodes[1]
    assert second_item.findtext("available") == "false"
    assert second_item.findtext("in_stock") == "false"
    assert len(second_item.findall("image")) == 0

    third_item = item_nodes[2]
    assert third_item.findtext("available") == ""
    assert third_item.findtext("in_stock") == "false"
    assert len(third_item.findall("image")) == 10

    # Ensure warnings logged for missing price and image
    warning_messages = " ".join(record.message for record in caplog.records)
    assert "Skipping item SKU-3" in warning_messages
    assert "Item SKU-2 missing main image" in warning_messages

