"""Tools for managing tag assignments across store and supplier products."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Sequence, Set

from .models import Product

# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass
class ModelTemplate:
    """Represents a brand/model template that should become a tag."""

    tag: str
    pattern: str
    category: str = "Модель пристрою"

    def __post_init__(self) -> None:
        flags = re.IGNORECASE
        self._regex = re.compile(self.pattern, flags)

    def iter_matches(self, text: str) -> Sequence[re.Match[str]]:
        return list(self._regex.finditer(text))


@dataclass
class TagRule:
    """A declarative rule that attaches tags based on regex checks."""

    name: str
    conditions: Sequence[str]
    tags: Sequence[str]
    auto_confirm: bool = True

    def matches(self, text: str) -> bool:
        return all(re.search(pattern, text, re.IGNORECASE) for pattern in self.conditions)


@dataclass
class TagAssignment:
    """Represents tag state for a single product inside the assignment board."""

    product: Product
    current_tags: Set[str] = field(default_factory=set)
    auto_tags: Set[str] = field(default_factory=set)
    rule_tags: Set[str] = field(default_factory=set)
    proposed_tags: Set[str] = field(default_factory=set)
    selected_tags: Set[str] = field(default_factory=set)
    category: str | None = None
    validation_errors: List[str] = field(default_factory=list)

    def all_effective_tags(self) -> Set[str]:
        return set(self.current_tags) | set(self.selected_tags)


# ---------------------------------------------------------------------------
# Configuration loading helpers
# ---------------------------------------------------------------------------


DEFAULT_MODEL_TEMPLATES: List[Dict[str, str]] = [
    {"tag": "Apple iPhone 15", "pattern": r"\\bApple iPhone 15\\b"},
    {"tag": "Apple iPhone 15 Pro", "pattern": r"\\bApple iPhone 15 Pro\\b"},
    {"tag": "Apple iPhone 15 Pro Max", "pattern": r"\\bApple iPhone 15 Pro Max\\b"},
    {"tag": "Xiaomi Redmi Note 12 4G", "pattern": r"\\bXiaomi Redmi Note 12 4G\\b"},
    {"tag": "Xiaomi Redmi Note 12 Pro", "pattern": r"\\bXiaomi Redmi Note 12 Pro\\b"},
    {"tag": "Samsung Galaxy S23", "pattern": r"\\bSamsung Galaxy S23\\b"},
    {"tag": "Samsung Galaxy S23 Ultra", "pattern": r"\\bSamsung Galaxy S23 Ultra\\b"},
    {"tag": "Realme 13", "pattern": r"\\bRealme 13\\b"},
]


DEFAULT_TAG_RULES_PAYLOAD: Dict[str, List[Dict[str, object]]] = {
    "rules": [
        {
            "name": "GETMAN Liquid Silk Camera",
            "conditions": [
                r"(?i)Liquid Silk Full Camera",
                r"(?i)GETMAN",
            ],
            "tags": ["Soft Case", "Чохол", "Camera Protective"],
            "auto_confirm": True,
        },
        {
            "name": "MagSafe chargers",
            "conditions": [r"(?i)MagSafe", r"(?i)charger"],
            "tags": ["Зарядка", "MagSafe"],
            "auto_confirm": True,
        },
    ]
}


TYPE_TAGS = {
    "чохол",
    "плівка",
    "кабель",
    "зарядка",
    "навушники",
    "павербанк",
}

COLOR_TAGS = {
    "black",
    "blue",
    "transparent",
    "pink",
    "red",
    "white",
    "green",
    "yellow",
    "purple",
    "orange",
    "gold",
    "silver",
    "gray",
    "grey",
    "brown",
    "beige",
}

MATERIAL_TAGS = {
    "tpu",
    "silicone",
    "silicon",
    "leather",
    "metal",
    "plastic",
    "glass",
    "carbon",
}

FUNCTION_TAGS = {
    "camera protective",
    "camera protection",
    "magsafe",
    "anti blue",
    "matte",
    "soft case",
}

BRAND_HINTS = {
    "apple",
    "samsung",
    "xiaomi",
    "redmi",
    "realme",
    "huawei",
    "oppo",
    "vivo",
    "poco",
    "oneplus",
    "nokia",
    "motorola",
    "google",
    "sony",
    "getman",
    "baseus",
}

MODEL_KEYWORDS = re.compile(
    r"(?i)\b(iphone|ipad|galaxy|s\d{1,2}|note|redmi|mi|poco|pixel|nova|mate|magic|honor|oneplus|motorola|razr|xperia|realme|oppo|vivo|watch)\b"
)


def default_templates_payload() -> Dict[str, List[Dict[str, str]]]:
    return {"models": list(DEFAULT_MODEL_TEMPLATES)}


def default_rules_payload() -> Dict[str, List[Dict[str, object]]]:
    return json.loads(json.dumps(DEFAULT_TAG_RULES_PAYLOAD))


def load_tag_templates(path: str | Path) -> List[ModelTemplate]:
    path = Path(path)
    if not path.exists():
        return [ModelTemplate(**item) for item in DEFAULT_MODEL_TEMPLATES]

    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)

    models: List[ModelTemplate] = []
    for item in payload.get("models", []):
        tag = item.get("tag")
        if not tag:
            continue
        pattern = item.get("pattern") or rf"\b{re.escape(tag)}\b"
        models.append(ModelTemplate(tag=tag, pattern=pattern))
    return models


def load_tag_rules(path: str | Path) -> List[TagRule]:
    path = Path(path)
    if not path.exists():
        payload = DEFAULT_TAG_RULES_PAYLOAD
    else:
        with path.open("r", encoding="utf-8") as fp:
            payload = json.load(fp)

    rules: List[TagRule] = []
    for item in payload.get("rules", []):
        name = item.get("name")
        tags = item.get("tags", [])
        conditions = item.get("conditions", [])
        if not name or not tags or not conditions:
            continue
        rules.append(
            TagRule(
                name=name,
                tags=list(tags),
                conditions=list(conditions),
                auto_confirm=bool(item.get("auto_confirm", True)),
            )
        )
    return rules


def make_assignment_key(
    supplier: str | None,
    sku: str | None,
    name: str,
) -> str:
    supplier_part = (supplier or "").strip() or "unknown"
    sku_part = (sku or "").strip() or name.strip()
    return f"{supplier_part}::{sku_part}"


def load_saved_tags(path: str | Path) -> Dict[str, Set[str]]:
    path = Path(path)
    if not path.exists():
        return {}

    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return {}

    assignments: Dict[str, Set[str]] = {}
    for entry in data:
        sku = entry.get("sku")
        name = entry.get("name")
        supplier = entry.get("supplier")
        tags = entry.get("tags", [])
        if not name:
            continue
        key = make_assignment_key(supplier, sku, name)
        assignments[key] = {tag for tag in tags if tag}
    return assignments


def save_tags(path: str | Path, assignments: Iterable[TagAssignment]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload: List[Dict[str, object]] = []
    for assignment in assignments:
        tags = sorted(assignment.all_effective_tags())
        if not tags:
            continue
        payload.append(
            {
                "sku": assignment.product.sku,
                "name": assignment.product.name,
                "supplier": assignment.product.supplier,
                "tags": tags,
            }
        )

    with path.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Tag inference helpers
# ---------------------------------------------------------------------------


def _filter_auto_tags(matches: List[tuple[ModelTemplate, re.Match[str]]]) -> Set[str]:
    filtered: Set[str] = set()
    for template, match in matches:
        candidate = template.tag
        candidate_lower = candidate.lower()
        has_longer = any(
            other_template.tag.lower().startswith(candidate_lower)
            and other_template.tag.lower() != candidate_lower
            for other_template, _ in matches
        )
        if has_longer:
            continue
        filtered.add(candidate)
    return filtered


def _is_model_tag(tag: str, templates: Sequence[ModelTemplate]) -> bool:
    lowered = tag.lower()
    if any(template.tag.lower() == lowered for template in templates):
        return True
    return bool(MODEL_KEYWORDS.search(tag))


def _is_color_tag(tag: str) -> bool:
    return tag.lower() in COLOR_TAGS


def _is_material_tag(tag: str) -> bool:
    return tag.lower() in MATERIAL_TAGS


def _is_function_tag(tag: str) -> bool:
    return tag.lower() in FUNCTION_TAGS


def _is_type_tag(tag: str) -> bool:
    return tag.lower() in TYPE_TAGS


def _is_brand_tag(tag: str) -> bool:
    lowered = tag.lower()
    if lowered in COLOR_TAGS | MATERIAL_TAGS | FUNCTION_TAGS | TYPE_TAGS:
        return False
    if lowered in BRAND_HINTS:
        return True
    stripped = tag.strip()
    if not stripped:
        return False
    if " " in stripped:
        parts = stripped.split()
        return all(part[0].isupper() for part in parts if part)
    return stripped[0].isupper() and len(stripped) > 2


def determine_category(tags: Iterable[str]) -> str | None:
    for tag in tags:
        if _is_type_tag(tag):
            return tag
    return None


def validate_tags(
    assignments: Iterable[TagAssignment],
    templates: Sequence[ModelTemplate] | None = None,
) -> List[TagAssignment]:
    templates = templates or []
    for assignment in assignments:
        combined = (
            set(assignment.current_tags)
            | set(assignment.selected_tags)
            | set(assignment.proposed_tags)
        )
        assignment.category = determine_category(combined)
        errors: List[str] = []

        if any(_is_type_tag(tag) for tag in combined):
            type_tags = {tag for tag in combined if _is_type_tag(tag)}
        else:
            type_tags = set()

        if "Чохол" in type_tags or "чохол" in (tag.lower() for tag in type_tags):
            if not any(_is_model_tag(tag, templates) for tag in combined):
                errors.append("Відсутній тег моделі")
            if not any(_is_color_tag(tag) for tag in combined):
                errors.append("Відсутній тег кольору")

        if "Павербанк" in type_tags or "павербанк" in (tag.lower() for tag in type_tags):
            if not any(_is_brand_tag(tag) for tag in combined):
                errors.append("Відсутній тег бренду")

        assignment.validation_errors = errors

    return list(assignments)


def apply_tags_to_products(
    products: Sequence[Product],
    templates: Sequence[ModelTemplate],
    rules: Sequence[TagRule],
    saved_tags: Mapping[str, Set[str]] | None = None,
) -> List[TagAssignment]:
    saved_tags = saved_tags or {}
    assignments: List[TagAssignment] = []

    for product in products:
        key = make_assignment_key(product.supplier, product.sku, product.name)
        current_tags = set(product.tags)
        if key in saved_tags:
            current_tags.update(saved_tags[key])

        name = product.name or ""

        matches: List[tuple[ModelTemplate, re.Match[str]]] = []
        for template in templates:
            matches.extend((template, match) for match in template.iter_matches(name))

        auto_tags = _filter_auto_tags(matches)

        rule_tags: Set[str] = set()
        for rule in rules:
            if rule.matches(name):
                rule_tags.update(tag for tag in rule.tags if tag)

        proposed = (auto_tags | rule_tags) - current_tags
        selected = {tag for tag in rule_tags if tag not in current_tags}

        assignment = TagAssignment(
            product=product,
            current_tags=current_tags,
            auto_tags=auto_tags,
            rule_tags=rule_tags,
            proposed_tags=proposed,
            selected_tags=selected if selected else set(),
        )
        assignments.append(assignment)

    return validate_tags(assignments, templates)


__all__ = [
    "ModelTemplate",
    "TagRule",
    "TagAssignment",
    "apply_tags_to_products",
    "default_rules_payload",
    "default_templates_payload",
    "determine_category",
    "load_saved_tags",
    "load_tag_rules",
    "load_tag_templates",
    "make_assignment_key",
    "save_tags",
    "validate_tags",
]

