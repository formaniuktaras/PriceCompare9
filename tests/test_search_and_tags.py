import re

from price_compare.model_templates import generate_model_pattern
from price_compare.models import PriceList, Product
from price_compare.search import ProductSearch
from price_compare.tags_assignment import ModelTemplate, match_models


def test_search_matches_noncontiguous_model_tokens() -> None:
    product = Product(
        sku="CASE-S23",
        name="TPU чохол Bonbon Metal Style with MagSafe для Samsung Galaxy S23",
        price=10.0,
    )
    price_list = PriceList(supplier="TestSupplier", products=[product])
    search = ProductSearch([price_list])

    results = search.search("Samsung S23")

    assert results, "Expected to find at least one search result"
    assert results[0].product is product


def test_model_template_fallback_matches_with_extra_words() -> None:
    template = ModelTemplate(
        category="Phones",
        brand="Samsung",
        name="S23",
        pattern=generate_model_pattern("Samsung", "S23"),
        tags=["Samsung", "S23"],
    )

    tags = match_models(
        "TPU чохол Bonbon Metal Style with MagSafe для Samsung Galaxy S23",
        [template],
    )

    assert "Samsung" in tags
    assert "S23" in tags


def test_generate_model_pattern_allows_missing_separators() -> None:
    pattern = generate_model_pattern("Samsung", "Galaxy S23 FE")
    regex = re.compile(pattern, re.IGNORECASE)

    assert regex.search("TPU для Samsung GalaxyS23FE (2023)")
    assert regex.search("Чохол Samsung Galaxy-S23 FE")


def test_match_models_returns_multiple_distinct_matches() -> None:
    templates = [
        ModelTemplate(
            category="Phones",
            brand="Xiaomi",
            name="Redmi 10X 5G",
            pattern=generate_model_pattern("Xiaomi", "Redmi 10X 5G"),
            tags=["Xiaomi Redmi 10X 5G"],
        ),
        ModelTemplate(
            category="Phones",
            brand="Xiaomi",
            name="Redmi 10X Pro 5G",
            pattern=generate_model_pattern("Xiaomi", "Redmi 10X Pro 5G"),
            tags=["Xiaomi Redmi 10X Pro 5G"],
        ),
        ModelTemplate(
            category="Phones",
            brand="Xiaomi",
            name="Redmi 10",
            pattern=generate_model_pattern("Xiaomi", "Redmi 10"),
            tags=["Xiaomi Redmi 10"],
        ),
    ]

    tags = match_models(
        "Захисна плівка SKLO Back (тил) Snake (тех.пак) для Xiaomi Redmi 10X 5G /10X Pro 5G",
        templates,
    )

    assert "Xiaomi Redmi 10X 5G" in tags
    assert "Xiaomi Redmi 10X Pro 5G" in tags
    assert "Xiaomi Redmi 10" not in tags


def test_match_models_ignores_matches_contained_in_longer_ones() -> None:
    templates = [
        ModelTemplate(
            category="Phones",
            brand="Apple",
            name="iPhone 15 Pro",
            pattern=generate_model_pattern("Apple", "iPhone 15 Pro"),
            tags=["iPhone 15 Pro"],
        ),
        ModelTemplate(
            category="Phones",
            brand="Apple",
            name="iPhone 15 Pro Max",
            pattern=generate_model_pattern("Apple", "iPhone 15 Pro Max"),
            tags=["iPhone 15 Pro Max"],
        ),
    ]

    tags = match_models("Чохол для Apple iPhone 15 Pro Max", templates)

    assert "iPhone 15 Pro Max" in tags
    assert "iPhone 15 Pro" not in tags
