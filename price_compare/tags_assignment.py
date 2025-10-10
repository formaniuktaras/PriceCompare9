"""Tools for managing tag assignments across store and supplier products."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Dict,
    Iterable,
    List,
    Mapping,
    MutableMapping,
    Optional,
    Sequence,
    Set,
    TYPE_CHECKING,
)

from .models import Product
from .model_templates import ModelTemplatesEditor

if TYPE_CHECKING:
    from .synonyms_manager import SynonymsManager

# ---------------------------------------------------------------------------
# Domain objects
# ---------------------------------------------------------------------------


@dataclass
class ModelTemplate:
    """Represents a compiled model template with metadata and tags."""

    category: str
    brand: str
    name: str
    pattern: str
    tags: Sequence[str]

    def __post_init__(self) -> None:
        try:
            self._regex: re.Pattern[str] | None = re.compile(self.pattern, re.IGNORECASE)
        except re.error:
            self._regex = None

    def iter_matches(self, text: str) -> Sequence[re.Match[str]]:
        if not self._regex:
            return []
        return list(self._regex.finditer(text))

    def match_length(self, text: str) -> int:
        """Return the maximum match length for the provided text."""

        matches = self.iter_matches(text)
        if not matches:
            return 0
        return max(len(match.group(0)) for match in matches)


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


DEFAULT_MODELS_PAYLOAD: Dict[str, object] = ModelTemplatesEditor.default_payload()


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


def default_templates_payload() -> Dict[str, object]:
    return json.loads(json.dumps(DEFAULT_MODELS_PAYLOAD))


def default_rules_payload() -> Dict[str, List[Dict[str, object]]]:
    return json.loads(json.dumps(DEFAULT_TAG_RULES_PAYLOAD))


def load_tag_templates(path: str | Path) -> List[ModelTemplate]:
    editor = ModelTemplatesEditor(path)
    templates: List[ModelTemplate] = []
    for record in editor.iter_model_records():
        templates.append(
            ModelTemplate(
                category=record.category,
                brand=record.brand,
                name=record.name,
                pattern=record.pattern,
                tags=list(record.tags),
            )
        )
    return templates


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


def _is_model_tag(tag: str, templates: Sequence[ModelTemplate]) -> bool:
    lowered = tag.lower()
    if any(template.name.lower() == lowered for template in templates):
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


def find_best_template_match(
    product_name: str, templates: Sequence[ModelTemplate]
) -> Optional[ModelTemplate]:
    """Return the template that has the longest exact regex match."""

    haystack = product_name or ""
    best: tuple[int, ModelTemplate] | None = None
    for template in templates:
        length = template.match_length(haystack)
        if length and (best is None or length > best[0]):
            best = (length, template)
    return best[1] if best else None


def find_best_model_match(
    product_name: str, models_list: Sequence[ModelTemplate]
) -> Optional[ModelTemplate]:
    """Compatibility wrapper that proxies to :func:`find_best_template_match`."""

    return find_best_template_match(product_name, models_list)


def match_models(product_name: str, templates: Sequence[ModelTemplate]) -> Set[str]:
    """Return a set of tags derived from the best matching model template."""

    template = find_best_template_match(product_name, templates)
    if not template:
        return set()
    return {tag for tag in template.tags if tag}


def apply_tag_rules(
    normalized_name: str,
    normalized_desc: str,
    rules: Sequence[TagRule],
) -> Set[str]:
    """Return tags derived from declarative rules for normalized text."""

    haystack = " ".join(
        part.strip()
        for part in (normalized_name or "", normalized_desc or "")
        if part and part.strip()
    )
    derived: Set[str] = set()
    for rule in rules:
        if rule.matches(haystack):
            derived.update(tag for tag in rule.tags if tag)
    return derived


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
    *,
    synonyms_manager: "SynonymsManager" | None = None,
) -> List[TagAssignment]:
    saved_tags = saved_tags or {}
    assignments: List[TagAssignment] = []

    if synonyms_manager is None:
        from .synonyms_manager import SynonymsManager  # Local import to avoid GUI dependency at module load

        synonyms_manager = SynonymsManager()

    for product in products:
        key = make_assignment_key(product.supplier, product.sku, product.name)
        current_tags = set(product.tags)
        if key in saved_tags:
            current_tags.update(saved_tags[key])

        name = product.name or ""
        description = product.description or ""

        normalized_payload = synonyms_manager.preprocess_product(
            {"name": name, "description": description}
        )
        normalized_name = normalized_payload.get("normalized_name", name.lower())
        normalized_desc = normalized_payload.get("normalized_desc", description.lower())

        product.extra["normalized_name"] = normalized_name
        product.extra["normalized_desc"] = normalized_desc
        setattr(product, "normalized_name", normalized_name)
        setattr(product, "normalized_desc", normalized_desc)

        auto_tags = match_models(normalized_name, templates)
        rule_tags = apply_tag_rules(normalized_name, normalized_desc, rules)

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
    "apply_tag_rules",
    "apply_tags_to_products",
    "default_rules_payload",
    "default_templates_payload",
    "determine_category",
    "find_best_model_match",
    "find_best_template_match",
    "load_saved_tags",
    "load_tag_rules",
    "load_tag_templates",
    "match_models",
    "make_assignment_key",
    "save_tags",
    "validate_tags",
]

