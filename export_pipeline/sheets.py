"""Helpers for interacting with Google Sheets via gspread."""

from __future__ import annotations

import logging
from typing import Iterable

import pandas as pd

LOGGER = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    import gspread
    from gspread_dataframe import get_as_dataframe, set_with_dataframe
except ImportError:  # pragma: no cover - optional dependency
    gspread = None  # type: ignore[assignment]
    get_as_dataframe = None  # type: ignore[assignment]
    set_with_dataframe = None  # type: ignore[assignment]


def _require_gspread() -> None:
    if gspread is None or set_with_dataframe is None or get_as_dataframe is None:
        raise RuntimeError(
            "gspread and gspread_dataframe are required for Google Sheets operations. "
            "Install them and provide service account credentials."
        )


def _open_spreadsheet(spreadsheet_id: str):  # pragma: no cover - thin wrapper
    _require_gspread()
    client = gspread.service_account()
    return client.open_by_key(spreadsheet_id)


def ensure_sheet(spreadsheet_id: str, title: str, headers: Iterable[str]) -> None:
    """Ensure the given sheet exists and has headers in the first row."""

    try:
        spreadsheet = _open_spreadsheet(spreadsheet_id)
        spreadsheet.worksheet(title)
    except RuntimeError as exc:
        LOGGER.warning("Skipping sheet ensure for %s/%s: %s", spreadsheet_id, title, exc)
        return
    except gspread.exceptions.WorksheetNotFound:  # type: ignore[attr-defined]
        worksheet = spreadsheet.add_worksheet(title=title, rows="1000", cols="26")
        if headers:
            worksheet.update("1:1", [list(headers)])
    else:
        # Sheet exists; nothing else to do.
        pass


def write_df(spreadsheet_id: str, title: str, df: pd.DataFrame) -> None:
    """Write the dataframe values into the given sheet."""

    try:
        spreadsheet = _open_spreadsheet(spreadsheet_id)
    except RuntimeError as exc:
        LOGGER.warning("Skipping sheet write for %s/%s: %s", spreadsheet_id, title, exc)
        return

    worksheet = spreadsheet.worksheet(title)
    set_with_dataframe(worksheet, df, include_index=False, include_column_header=True, resize=True)


def read_df(spreadsheet_id: str, title: str) -> pd.DataFrame:
    """Read a sheet into a pandas DataFrame."""

    spreadsheet = _open_spreadsheet(spreadsheet_id)
    worksheet = spreadsheet.worksheet(title)
    df = get_as_dataframe(worksheet, evaluate_formulas=False)
    return df
