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
