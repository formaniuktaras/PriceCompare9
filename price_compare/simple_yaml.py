"""Minimal YAML loader used when PyYAML is unavailable.

The project relies on a very small subset of YAML features for reading
configuration files: nested mappings, lists introduced with ``-`` and
inline JSON structures (``{"key": "value"}``, ``[1, 2, 3]``).  Shipping a
full YAML parser would significantly increase the binary size of the
PyInstaller build.  Instead we provide a lightweight fallback
implementation that understands exactly the constructs used in the
configuration files bundled with the application.

The goal of this module is not to be a perfect YAML interpreter; it only
aims to be good enough for our configuration schema.  The implementation
prefers readability over ultimate generality and keeps the parsing logic
contained in a few helper functions.  When the optional ``pyyaml``
dependency is installed this module is never imported – ``config.py``
will use the real loader.
"""

from __future__ import annotations

from dataclasses import dataclass
import ast
from typing import Iterable, List, Tuple

__all__ = ["safe_load"]


def safe_load(stream: str | Iterable[str]) -> object:
    """Parse *stream* and return the corresponding Python object.

    Parameters
    ----------
    stream:
        Either a string containing the YAML document or an iterable of
        lines (e.g. a file handle).
    """

    if hasattr(stream, "read"):
        text = stream.read()
    elif isinstance(stream, str):
        text = stream
    else:
        text = "".join(stream)

    lines = _preprocess_lines(text.splitlines())
    if not lines:
        return None

    value, index = _parse_block(lines, 0, 0)
    # Consume trailing empty/comment-only lines.
    while index < len(lines) and not lines[index].content:
        index += 1
    if index != len(lines):
        raise ValueError("Unexpected content at end of YAML document")
    return value


@dataclass(frozen=True)
class _Line:
    raw: str
    indent: int
    content: str


def _preprocess_lines(lines: List[str]) -> List[_Line]:
    processed: List[_Line] = []
    for raw in lines:
        clean = _strip_comment(raw.rstrip())
        if not clean.strip():
            processed.append(_Line(raw=raw, indent=0, content=""))
            continue
        indent = len(clean) - len(clean.lstrip(" "))
        processed.append(_Line(raw=raw, indent=indent, content=clean.strip()))
    return processed


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for idx, char in enumerate(line):
        if char == "\\" and (in_single or in_double):
            escaped = not escaped
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            return line[:idx]
        else:
            escaped = False
    return line


def _parse_block(lines: List[_Line], index: int, indent: int) -> Tuple[object, int]:
    while index < len(lines) and not lines[index].content:
        index += 1
    if index >= len(lines):
        return None, index

    line = lines[index]
    if line.indent < indent:
        return None, index
    if line.content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: List[_Line], index: int, indent: int) -> Tuple[dict, int]:
    mapping: dict = {}
    while index < len(lines):
        line = lines[index]
        if line.indent < indent:
            break
        if not line.content:
            index += 1
            continue
        if line.content.startswith("- "):
            break

        key, sep, remainder = line.content.partition(":")
        if not sep:
            raise ValueError(f"Invalid mapping entry: {line.raw}")
        key = key.strip()
        remainder = remainder.strip()
        index += 1

        if remainder:
            value = _parse_scalar(remainder)
        else:
            value, index = _parse_block(lines, index, indent + 2)
        mapping[key] = value

    return mapping, index


def _parse_list(lines: List[_Line], index: int, indent: int) -> Tuple[list, int]:
    items: list = []
    while index < len(lines):
        line = lines[index]
        if line.indent < indent or not line.content:
            break
        if not line.content.startswith("- "):
            break

        item_text = line.content[2:].strip()
        index += 1

        if item_text:
            items.append(_parse_list_item_with_text(lines, item_text, indent, index))
            index = _advance_past_child_block(lines, index, indent + 2)
        else:
            value, index = _parse_block(lines, index, indent + 2)
            items.append(value)

    return items, index


def _advance_past_child_block(lines: List[_Line], index: int, child_indent: int) -> int:
    current = index
    while current < len(lines):
        line = lines[current]
        if not line.content:
            current += 1
            continue
        if line.indent < child_indent:
            break
        if line.indent == child_indent and line.content.startswith("- "):
            break
        current += 1
    return current


def _parse_list_item_with_text(
    lines: List[_Line], item_text: str, indent: int, index: int
) -> object:
    if item_text.startswith("{") or item_text.startswith("["):
        return _parse_literal(item_text)

    if ":" not in item_text:
        return _parse_scalar(item_text)

    # Treat the inline ``key: value`` pair as the start of a mapping and
    # merge it with any following lines that share the increased indent.
    pseudo_line = _Line(raw=item_text, indent=indent + 2, content=item_text)
    slice_start = index
    slice_lines: List[_Line] = [pseudo_line]

    while slice_start < len(lines):
        candidate = lines[slice_start]
        if not candidate.content:
            slice_lines.append(candidate)
            slice_start += 1
            continue
        if candidate.indent < indent + 2:
            break
        if candidate.indent == indent and candidate.content.startswith("- "):
            break
        slice_lines.append(candidate)
        slice_start += 1

    mapping, _ = _parse_mapping(slice_lines, 0, indent + 2)
    return mapping


def _parse_scalar(value: str) -> object:
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass

    literal = _parse_literal(value)
    if literal is not None:
        return literal
    return value


def _parse_literal(value: str) -> object | None:
    if not value:
        return None
    if value[0] in "'\"[{":
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            if value.startswith("{") and value.endswith("}"):
                return _parse_inline_mapping(value)
            raise ValueError(f"Unable to parse literal: {value}") from None
    return None


def _parse_inline_mapping(value: str) -> dict:
    inner = value[1:-1].strip()
    if not inner:
        return {}

    result: dict = {}
    for item in _split_top_level(inner, ","):
        key_part, sep, value_part = item.partition(":")
        if not sep:
            raise ValueError(f"Invalid inline mapping entry: {item}")
        key = _unquote_if_needed(key_part.strip())
        result[key] = _parse_scalar(value_part.strip())
    return result


def _split_top_level(value: str, delimiter: str) -> List[str]:
    parts: List[str] = []
    current: List[str] = []
    depth = 0
    in_single = False
    in_double = False
    escape = False

    for char in value:
        if in_single:
            current.append(char)
            if char == "'" and not escape:
                in_single = False
            escape = char == "\\" and not escape
            continue
        if in_double:
            current.append(char)
            if char == '"' and not escape:
                in_double = False
            escape = char == "\\" and not escape
            continue

        if char == "'":
            in_single = True
            current.append(char)
            continue
        if char == '"':
            in_double = True
            current.append(char)
            continue

        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(depth - 1, 0)

        if char == delimiter and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)

    if current:
        parts.append("".join(current).strip())
    return [part for part in parts if part]


def _unquote_if_needed(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}:
        return ast.literal_eval(token)
    return token

