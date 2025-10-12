"""Graphical user interface for the price comparison toolkit."""

from __future__ import annotations

import ast
import json
import re
import threading
import tkinter as tk
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import (
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    TypeVar,
    TYPE_CHECKING,
)

from .comparison_engine import ComparisonBuilder, ComparisonGroup, SupplierMatch
from .comparison_state import ComparisonStateStore
from .io import MissingRequiredColumnsError, PriceListExporter, PriceListImporter
from .model_templates import ModelTemplatesEditor
from .models import PriceList, Product
from .repository import PriceListRepository
from .search import ProductSearch
from .tagging import Tagger
from . import tags_assignment
from .templates import ImportTemplate, ImportTemplateStore

if TYPE_CHECKING:
    from .synonyms_manager import SynonymsManager


def _format_price(product: Product) -> str:
    return f"{product.price:,.2f} {product.currency}".replace(",", " ")


STORE_PRICE_FILENAME = "store_price.json"
STORE_SUPPLIER_NAME = "Мій інтернет-магазин"


T = TypeVar("T")


def _center_dialog(window: tk.Toplevel, master: tk.Misc) -> str:
    master.update_idletasks()
    window.update_idletasks()
    width = window.winfo_width()
    height = window.winfo_height()
    master_width = master.winfo_width() or window.winfo_width()
    master_height = master.winfo_height() or window.winfo_height()
    x = master.winfo_rootx() + max((master_width - width) // 2, 0)
    y = master.winfo_rooty() + max((master_height - height) // 2, 0)
    return f"+{x}+{y}"


def _plural_form(count: int, forms: tuple[str, str, str]) -> str:
    count = abs(int(count))
    if count % 10 == 1 and count % 100 != 11:
        return forms[0]
    if count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        return forms[1]
    return forms[2]


class ProgressDialog(tk.Toplevel):
    """Modal dialog that shows an indeterminate progress bar."""

    def __init__(self, master: tk.Misc, *, title: str, message: str) -> None:
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        ttk.Label(container, text=message, wraplength=320, justify=tk.LEFT).pack(
            fill=tk.X
        )
        self.progress = ttk.Progressbar(container, mode="indeterminate", length=320)
        self.progress.pack(fill=tk.X, pady=(12, 0))
        self.progress.start(12)

        self.update_idletasks()
        if master.winfo_viewable():
            self.geometry(_center_dialog(self, master))

    def close(self) -> None:
        try:
            self.progress.stop()
        except tk.TclError:
            pass
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()


class PriceCompareApp(tk.Tk):
    """Desktop application for managing and comparing supplier price lists."""

    def __init__(
        self,
        *,
        data_dir: str | Path = ".price_compare_data",
        tags_config: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.title("Price Compare")
        self.geometry("1180x720")
        self.minsize(960, 640)

        self.repository = PriceListRepository(data_dir=data_dir)
        self.importer = PriceListImporter()
        self.exporter = PriceListExporter()
        self.template_store = ImportTemplateStore(data_dir=data_dir)
        self.tagger = Tagger.from_json(tags_config) if tags_config else Tagger()
        self._tags_config_path: str | None = str(tags_config) if tags_config else None

        self.synonyms_path = self.repository.data_dir / "synonyms.json"

        self.comparison_state = ComparisonStateStore(
            self.repository.data_dir / "comparison_state.json"
        )

        self.price_lists: Dict[str, PriceList] = {}
        self.store_price_list: PriceList | None = None
        self._store_price_path = self.repository.data_dir / STORE_PRICE_FILENAME
        self._comparison_refresh_in_progress = False
        self._catalog_links_button: ttk.Button | None = None
        self._comparison_subset: Set[str] | None = None

        self._create_menu()
        self._create_widgets()
        self.refresh_data(mark_comparisons_stale=False)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _create_widgets(self) -> None:
        container = ttk.Notebook(self)
        container.pack(fill=tk.BOTH, expand=True)

        self.main_price_tab = ttk.Frame(container)
        self.catalog_tab = ttk.Frame(container)
        self.search_tab = ttk.Frame(container)
        self.tags_tab = ttk.Frame(container)
        self.compare_tab = ttk.Frame(container)

        container.add(self.main_price_tab, text="Основний прайс")
        container.add(self.catalog_tab, text="Каталог постачальників")
        container.add(self.search_tab, text="Пошук")
        container.add(self.tags_tab, text="Мітки")
        container.add(self.compare_tab, text="Порівняння")

        self._build_main_price_tab()
        self._build_catalog_tab()
        self._build_search_tab()
        self._build_tags_tab()
        self._build_compare_tab()

    def _set_links_button_state(self, enabled: bool) -> None:
        if self._catalog_links_button is None:
            return
        state = tk.NORMAL if enabled else tk.DISABLED
        self._catalog_links_button.config(state=state)

    def _mark_comparisons_stale(self) -> None:
        if hasattr(self, "comparison_board"):
            self.comparison_board.mark_stale()
        if not self._comparison_refresh_in_progress:
            self._set_links_button_state(bool(self.price_lists or self.store_price_list))

    def _run_background_task(
        self,
        *,
        title: str,
        message: str,
        task: Callable[[], T],
        on_success: Callable[[T], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        error_message: str | None = None,
    ) -> None:
        dialog = ProgressDialog(self, title=title, message=message)

        def handle_success(result: T) -> None:
            dialog.close()
            if on_success:
                on_success(result)

        def handle_error(exc: Exception) -> None:
            dialog.close()
            if on_error:
                on_error(exc)
            else:
                details = error_message or "Сталася помилка під час виконання операції."
                messagebox.showerror(title, f"{details}\n\n{exc}")

        def worker() -> None:
            try:
                result = task()
            except Exception as exc:  # pragma: no cover - background thread
                self.after(0, lambda: handle_error(exc))
            else:
                self.after(0, lambda: handle_success(result))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _create_menu(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        settings_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Налаштування", menu=settings_menu)
        settings_menu.add_command(
            label="Синоніми",
            command=self._open_synonyms_manager,
        )
        settings_menu.add_separator()
        settings_menu.add_command(
            label="Завантажити правила тегування",
            command=self._load_tag_rules,
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="Вийти", command=self.destroy)

    def _open_synonyms_manager(self) -> None:
        try:
            from .synonyms_manager import open_synonyms_window
        except Exception as exc:  # pragma: no cover - defensive UI path
            messagebox.showerror(
                "Синоніми",
                f"Не вдалося відкрити редактор синонімів.\n\n{exc}",
            )
            return

        path = self.synonyms_path
        open_synonyms_window(path)
        if hasattr(self, "tags_board"):
            self.tags_board.invalidate_synonyms_cache()

    def _build_main_price_tab(self) -> None:
        toolbar = ttk.Frame(self.main_price_tab)
        toolbar.pack(fill=tk.X, padx=8, pady=8)

        ttk.Button(
            toolbar,
            text="Завантажити прайс магазину",
            command=self._import_store_price,
        ).pack(side=tk.LEFT)

        self.store_status_var = tk.StringVar(value="Прайс не завантажено.")
        ttk.Label(toolbar, textvariable=self.store_status_var).pack(
            side=tk.LEFT, padx=(12, 0)
        )

        columns = ("sku", "name", "price", "tags")
        self.store_tree = ttk.Treeview(
            self.main_price_tab,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headers = {
            "sku": "SKU",
            "name": "Назва",
            "price": "Ціна",
            "tags": "Теги",
        }
        widths = {"sku": 140, "name": 360, "price": 120, "tags": 220}
        for column in columns:
            self.store_tree.heading(column, text=headers[column])
            self.store_tree.column(column, width=widths[column], anchor=tk.W)
        self.store_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    def _build_catalog_tab(self) -> None:
        toolbar = ttk.Frame(self.catalog_tab)
        toolbar.pack(fill=tk.X, padx=8, pady=4)

        ttk.Button(toolbar, text="Імпорт прайсу", command=self._handle_import_price).pack(
            side=tk.LEFT
        )
        ttk.Button(toolbar, text="Експорт прайсу", command=self._export_selected_prices).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(toolbar, text="Видалити", command=self._delete_selected_prices).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self._catalog_links_button = ttk.Button(
            toolbar,
            text="Оновити зв'язки",
            command=self._start_comparison_refresh,
        )
        self._catalog_links_button.pack(side=tk.LEFT, padx=(8, 0))

        columns = ("actions", "updated")
        self.catalog_tree = ttk.Treeview(
            self.catalog_tab,
            columns=columns,
            show="tree headings",
            selectmode="extended",
        )
        self.catalog_tree.heading("#0", text="Постачальники / Прайси", anchor=tk.W)
        self.catalog_tree.heading("actions", text="Дії", anchor=tk.CENTER)
        self.catalog_tree.heading("updated", text="Оновлено", anchor=tk.W)
        self.catalog_tree.column("#0", width=420, anchor=tk.W, stretch=True)
        self.catalog_tree.column("actions", width=80, anchor=tk.CENTER, stretch=False)
        self.catalog_tree.column("updated", width=180, anchor=tk.W, stretch=False)
        self.catalog_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))
        self.catalog_tree.tag_configure("placeholder", foreground="#888888")
        self.catalog_tree.bind("<Button-1>", self._on_catalog_tree_click, add="+")
        self.catalog_tree.bind("<Button-3>", self._on_catalog_context_request)
        self.catalog_tree.bind("<Delete>", lambda _event: self._delete_selected_prices())
        self.catalog_tree.bind("<Insert>", lambda _event: self._add_supplier())
        self.catalog_tree.bind("<Shift-Z>", lambda _event: self._rename_supplier())
        self.catalog_tree.bind("<Shift-z>", lambda _event: self._rename_supplier())
        self.bind_all("<Control-r>", lambda _event: self._start_comparison_refresh())

        bottom_bar = ttk.Frame(self.catalog_tab)
        bottom_bar.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(bottom_bar, text="+", width=4, command=self._add_supplier).pack(
            side=tk.LEFT
        )
        ttk.Button(bottom_bar, text="-", width=4, command=self._remove_supplier).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(bottom_bar, text="✎", width=4, command=self._rename_supplier).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self._supplier_menu = tk.Menu(self, tearoff=False)
        self._supplier_menu.add_command(
            label="Додати прайс", command=lambda: self._handle_import_price(context="supplier")
        )
        self._supplier_menu.add_command(
            label="Імпорт прайсу", command=lambda: self._handle_import_price(context="supplier")
        )
        self._supplier_menu.add_command(
            label="Перейменувати", command=self._rename_supplier
        )
        self._supplier_menu.add_command(
            label="Видалити постачальника", command=self._remove_supplier
        )

        self._price_menu = tk.Menu(self, tearoff=False)
        self._price_menu.add_command(label="Оновити файл…", command=self._refresh_price_file)
        self._price_menu.add_command(
            label="Налаштувати автооновлення…", command=self._configure_auto_update
        )
        self._price_menu.add_command(label="Експорт", command=self._export_selected_prices)
        self._price_menu.add_command(label="Видалити", command=self._delete_selected_prices)

        self._catalog_tree_items: Dict[str, dict] = {}

    def _build_search_tab(self) -> None:
        control_frame = ttk.LabelFrame(self.search_tab, text="Параметри пошуку")
        control_frame.pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(control_frame, text="Запит:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self.search_query = ttk.Entry(control_frame)
        self.search_query.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(control_frame, text="Постачальник:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)
        self.search_supplier = ttk.Combobox(control_frame, state="readonly")
        self.search_supplier.grid(row=0, column=3, sticky=tk.W, padx=4, pady=4)

        self.fuzzy_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(control_frame, text="Нечіткий пошук", variable=self.fuzzy_var).grid(
            row=1, column=0, sticky=tk.W, padx=4, pady=4
        )

        ttk.Label(control_frame, text="Поріг схожості:").grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=4
        )
        self.threshold_var = tk.DoubleVar(value=0.6)
        ttk.Scale(control_frame, from_=0.3, to=1.0, orient=tk.HORIZONTAL, variable=self.threshold_var).grid(
            row=1, column=2, sticky=tk.EW, padx=4, pady=4
        )

        ttk.Label(control_frame, text="Ліміт результатів:").grid(
            row=1, column=3, sticky=tk.W, padx=4, pady=4
        )
        self.limit_var = tk.IntVar(value=20)
        ttk.Spinbox(control_frame, from_=5, to=200, textvariable=self.limit_var, width=6).grid(
            row=1, column=4, sticky=tk.W, padx=4, pady=4
        )

        control_frame.columnconfigure(1, weight=1)
        control_frame.columnconfigure(2, weight=1)

        actions = ttk.Frame(self.search_tab)
        actions.pack(fill=tk.X, padx=8)
        ttk.Button(actions, text="Пошук", command=self._perform_search).pack(side=tk.LEFT)
        ttk.Button(actions, text="Експорт результатів", command=self._export_search_results).pack(
            side=tk.LEFT, padx=8
        )

        columns = ("sku", "name", "price", "supplier", "score")
        self.search_tree = ttk.Treeview(
            self.search_tab,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headers = {
            "sku": "SKU",
            "name": "Назва",
            "price": "Ціна",
            "supplier": "Постачальник",
            "score": "Оцінка",
        }
        widths = {"sku": 140, "name": 360, "price": 120, "supplier": 160, "score": 80}
        for column in columns:
            self.search_tree.heading(column, text=headers[column])
            self.search_tree.column(column, width=widths[column], anchor=tk.W)
        self.search_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.search_results: List[Product] = []

    def _build_tags_tab(self) -> None:
        self.tags_board = TagsTab(
            self.tags_tab,
            data_dir=self.repository.data_dir,
            synonyms_path=self.synonyms_path,
            get_products=self._all_products_for_tags,
            on_save=self._on_tags_saved,
        )
        self.tags_board.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    def _build_compare_tab(self) -> None:
        self.comparison_board = ComparisonBoard(
            self.compare_tab,
            get_store_products=self._current_store_products,
            get_supplier_lists=self._comparison_supplier_lists,
            state_store=self.comparison_state,
            on_reload=self._start_comparison_refresh,
        )
        self.comparison_board.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def _start_comparison_refresh(self) -> None:
        if not hasattr(self, "comparison_board"):
            return
        if self._comparison_refresh_in_progress:
            return

        subset: Set[str] | None = None
        if hasattr(self, "catalog_tree"):
            selected_prices = self._selected_price_nodes()
            suppliers = {meta.get("supplier") for meta in selected_prices if meta.get("supplier")}
            if suppliers:
                subset = {str(name) for name in suppliers if name}
        self._comparison_subset = subset

        if not self.price_lists and not self.store_price_list:
            messagebox.showinfo(
                "Оновлення зв'язків",
                "Спочатку імпортуйте принаймні один прайс або завантажте прайс магазину.",
            )
            return

        self._comparison_refresh_in_progress = True
        self._set_links_button_state(False)
        self.comparison_board.set_busy(True)

        def on_success(rows: Sequence[ComparisonRow]) -> None:
            self._comparison_refresh_in_progress = False
            self.comparison_board.refresh(rows=rows)
            self.comparison_board.set_busy(False)
            self._set_links_button_state(bool(self.price_lists or self.store_price_list))

        def on_error(exc: Exception) -> None:
            self._comparison_refresh_in_progress = False
            self.comparison_board.set_busy(False)
            self.comparison_board.mark_stale()
            self._set_links_button_state(bool(self.price_lists or self.store_price_list))
            messagebox.showerror(
                "Оновлення зв'язків",
                f"Не вдалося оновити співставлення: {exc}",
            )

        self._run_background_task(
            title="Оновлення зв'язків",
            message="Будь ласка, зачекайте. Виконується оновлення співставлень прайсів…",
            task=self.comparison_board.build_rows,
            on_success=on_success,
            on_error=on_error,
        )

    def refresh_data(self, *, mark_comparisons_stale: bool = True) -> None:
        try:
            self.price_lists = self.repository.load_all()
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося завантажити дані: {exc}")
            self.price_lists = {}
        self._comparison_subset = None
        self._populate_catalog_tree()
        self._populate_supplier_dropdown()
        self._load_store_price_from_disk()
        self._update_store_tree()
        has_any_prices = bool(self.price_lists or self.store_price_list)
        if not self._comparison_refresh_in_progress:
            self._set_links_button_state(has_any_prices)
        if mark_comparisons_stale:
            self._mark_comparisons_stale()
        if hasattr(self, "tags_board"):
            self.tags_board.refresh_products()
        if hasattr(self, "comparison_board"):
            if not mark_comparisons_stale:
                self.comparison_board.refresh()

    def _populate_supplier_dropdown(self) -> None:
        suppliers = sorted(self.price_lists)
        self.search_supplier["values"] = ["Усі"] + suppliers
        self.search_supplier.set("Усі")

    def _comparison_supplier_lists(self) -> Sequence[PriceList]:
        if self._comparison_subset:
            return [
                self.price_lists[name]
                for name in self._comparison_subset
                if name in self.price_lists
            ]
        return list(self.price_lists.values())

    def _format_relative_time(self, raw: str | None) -> str:
        if not raw:
            return "—"
        try:
            normalized = raw.replace("Z", "+00:00")
            moment = datetime.fromisoformat(normalized)
        except ValueError:
            return raw
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - moment
        seconds = int(max(delta.total_seconds(), 0))
        if seconds < 60:
            return "щойно"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes} хв тому"
        hours = minutes // 60
        minutes = minutes % 60
        if hours < 24:
            parts = [f"{hours} год"]
            if minutes:
                parts.append(f"{minutes} хв")
            return " ".join(parts) + " тому"
        days = hours // 24
        if days < 30:
            form = _plural_form(days, ("день", "дні", "днів"))
            return f"{days} {form} тому"
        months = days // 30
        if months < 12:
            form = _plural_form(months, ("місяць", "місяці", "місяців"))
            return f"{months} {form} тому"
        years = months // 12
        form = _plural_form(years, ("рік", "роки", "років"))
        return f"{years} {form} тому"

    def _catalog_item_key(self, item_id: str) -> tuple[str, str, str | None] | None:
        meta = self._catalog_tree_items.get(item_id)
        if not meta:
            return None
        kind = meta.get("type")
        supplier = meta.get("supplier")
        if not supplier:
            return None
        if kind == "supplier":
            return ("supplier", supplier, None)
        if kind == "price":
            return ("price", supplier, meta.get("price_id") or "default")
        return None

    def _extract_price_entries(self, price_list: PriceList) -> List[Dict[str, object]]:
        metadata = dict(price_list.metadata or {})
        entries: List[Dict[str, object]] = []
        prices_meta = metadata.get("prices")
        if isinstance(prices_meta, list):
            for entry in prices_meta:
                if not isinstance(entry, dict):
                    continue
                price_id = str(entry.get("id") or entry.get("name") or "default")
                name = str(entry.get("name") or "Прайс")
                updated_at = entry.get("updated_at")
                actions = "↻  ⚙"
                entries.append(
                    {
                        "id": price_id,
                        "name": name,
                        "updated_at": updated_at,
                        "updated_label": self._format_relative_time(str(updated_at) if updated_at else None),
                        "auto_update": entry.get("auto_update", {}),
                        "actions": actions,
                    }
                )
        if not entries and (
            price_list.products
            or metadata.get("price_name")
            or metadata.get("updated_at")
        ):
            updated_at = metadata.get("updated_at")
            name = metadata.get("price_name") or "Прайс"
            entries.append(
                {
                    "id": "default",
                    "name": str(name),
                    "updated_at": updated_at,
                    "updated_label": self._format_relative_time(
                        str(updated_at) if updated_at else None
                    ),
                    "auto_update": metadata.get("auto_update", {}),
                    "actions": "↻  ⚙",
                }
            )
        return entries

    def _populate_catalog_tree(self) -> None:
        if not hasattr(self, "catalog_tree"):
            return
        expanded: Dict[str, bool] = {}
        selected_keys: Set[tuple[str, str, str | None]] = set()
        for item_id in self.catalog_tree.get_children(""):
            key = self._catalog_item_key(item_id)
            if key and key[0] == "supplier":
                expanded[key[1]] = bool(self.catalog_tree.item(item_id, "open"))
        for item_id in self.catalog_tree.selection():
            key = self._catalog_item_key(item_id)
            if key:
                selected_keys.add(key)

        for item in self.catalog_tree.get_children(""):
            self.catalog_tree.delete(item)
        self._catalog_tree_items.clear()

        for supplier in sorted(self.price_lists):
            price_list = self.price_lists[supplier]
            supplier_item = self.catalog_tree.insert(
                "",
                tk.END,
                text=supplier,
                open=expanded.get(supplier, True),
            )
            self._catalog_tree_items[supplier_item] = {"type": "supplier", "supplier": supplier}
            price_entries = self._extract_price_entries(price_list)
            has_price = False
            for entry in price_entries:
                name = entry["name"]
                updated_label = entry["updated_label"]
                actions = entry["actions"]
                price_item = self.catalog_tree.insert(
                    supplier_item,
                    tk.END,
                    text=name,
                    values=(actions, updated_label),
                )
                self._catalog_tree_items[price_item] = {
                    "type": "price",
                    "supplier": supplier,
                    "price_id": entry["id"],
                    "name": name,
                    "auto_update": entry.get("auto_update") or {},
                }
                if ("price", supplier, entry["id"]) in selected_keys:
                    self.catalog_tree.selection_add(price_item)
                has_price = True
            if not has_price:
                placeholder = self.catalog_tree.insert(
                    supplier_item,
                    tk.END,
                    text="(немає прайсів)",
                    values=("", ""),
                    tags=("placeholder",),
                )
                self._catalog_tree_items[placeholder] = {
                    "type": "placeholder",
                    "supplier": supplier,
                }
            if ("supplier", supplier, None) in selected_keys:
                self.catalog_tree.selection_add(supplier_item)

    def _selected_supplier_from_tree(self) -> str | None:
        if not hasattr(self, "catalog_tree"):
            return None
        selection = self.catalog_tree.selection()
        if not selection:
            return None
        first = selection[0]
        meta = self._catalog_tree_items.get(first)
        if not meta:
            return None
        if meta.get("type") in {"supplier", "placeholder", "price"}:
            return meta.get("supplier")
        return None

    def _selected_price_nodes(self) -> List[Dict[str, object]]:
        if not hasattr(self, "catalog_tree"):
            return []
        seen: Set[tuple[str, str]] = set()
        results: List[Dict[str, object]] = []
        for item_id in self.catalog_tree.selection():
            meta = self._catalog_tree_items.get(item_id)
            if not meta:
                continue
            if meta.get("type") == "price":
                key = (meta.get("supplier"), meta.get("price_id"))
                if key not in seen:
                    seen.add(key)
                    results.append(meta)
            elif meta.get("type") == "supplier":
                for child in self.catalog_tree.get_children(item_id):
                    child_meta = self._catalog_tree_items.get(child)
                    if not child_meta or child_meta.get("type") != "price":
                        continue
                    key = (child_meta.get("supplier"), child_meta.get("price_id"))
                    if key in seen:
                        continue
                    seen.add(key)
                    results.append(child_meta)
        return results

    def _handle_import_price(self, context: str | None = None) -> None:
        supplier = self._selected_supplier_from_tree()
        if not supplier and context == "supplier":
            messagebox.showwarning("Імпорт прайсу", "Оберіть постачальника у списку.")
            return
        if not supplier:
            supplier = simpledialog.askstring("Постачальник", "Назва постачальника:")
            if not supplier:
                messagebox.showinfo("Імпорт перервано", "Назва постачальника не вказана.")
                return
        self._import_price_list(supplier=supplier)

    def _on_catalog_tree_click(self, event: tk.Event) -> str | None:
        if not hasattr(self, "catalog_tree"):
            return None
        item_id = self.catalog_tree.identify_row(event.y)
        column = self.catalog_tree.identify_column(event.x)
        if not item_id or column != "#1":
            return None
        meta = self._catalog_tree_items.get(item_id)
        if not meta or meta.get("type") != "price":
            return None
        bbox = self.catalog_tree.bbox(item_id, "actions")
        if not bbox:
            return None
        x1, _y1, width, _height = bbox
        relative_x = event.x - x1
        if relative_x < 0 or width <= 0:
            return None
        self.catalog_tree.selection_set(item_id)
        if relative_x <= width / 2:
            self._refresh_price_file()
        else:
            self._configure_auto_update()
        return "break"

    def _on_catalog_context_request(self, event: tk.Event) -> None:
        if not hasattr(self, "catalog_tree"):
            return
        item_id = self.catalog_tree.identify_row(event.y)
        if not item_id:
            return
        self.catalog_tree.selection_set(item_id)
        meta = self._catalog_tree_items.get(item_id)
        if not meta:
            return
        menu = self._supplier_menu if meta.get("type") != "price" else self._price_menu
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        

    # ------------------------------------------------------------------
    # Import / export actions
    # ------------------------------------------------------------------
    def _import_price_list(self, supplier: str | None = None) -> None:
        path = filedialog.askopenfilename(
            title="Оберіть файл прайсу",
            filetypes=(
                ("CSV файли", "*.csv"),
                ("JSON файли", "*.json"),
                ("XML файли", "*.xml"),
                ("Excel файли", "*.xlsx"),
                ("Усі підтримувані", "*.csv *.json *.xml *.xlsx"),
            ),
        )
        if not path:
            return

        if supplier is None:
            supplier = simpledialog.askstring("Постачальник", "Назва постачальника:")
        if not supplier:
            messagebox.showinfo("Імпорт перервано", "Назва постачальника не вказана.")
            return

        try:
            headers, preview_rows = self.importer.peek(path, limit=15)
        except Exception as exc:
            messagebox.showerror(
                "Помилка імпорту",
                f"Не вдалося проаналізувати файл: {exc}",
            )
            return

        template = self.template_store.get_template(supplier)
        available_templates = self.template_store.list_templates()

        suggested_mapping = self.importer.suggest_mapping(
            headers,
            column_mapping=template.column_mapping if template else None,
        )
        initial_mapping = dict(suggested_mapping)
        if template:
            for field, column in template.column_mapping.items():
                if column in headers:
                    initial_mapping[field] = column

        dialog = ImportSettingsDialog(
            self,
            path=path,
            supplier=supplier,
            headers=headers,
            preview_rows=preview_rows,
            required_fields=sorted(self.importer.required_fields),
            optional_fields=list(self.importer.optional_fields),
            initial_mapping=initial_mapping,
            templates=available_templates,
            current_template=template,
        )
        self.wait_window(dialog)

        if not dialog.result:
            return

        column_mapping = dialog.result["column_mapping"]
        save_template = dialog.result["save_template"]

        existing_metadata = {}
        if supplier in self.price_lists:
            existing_metadata = dict(self.price_lists[supplier].metadata)

        def task() -> PriceList:
            price_list = self.importer.load(
                path, supplier=supplier, column_mapping=column_mapping
            )
            self.tagger.apply(price_list.products)
            metadata = dict(existing_metadata)
            prices_meta = metadata.get("prices")
            if not isinstance(prices_meta, list):
                prices_meta = []
            price_entry: Dict[str, object] | None = None
            for entry in prices_meta:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("id") or entry.get("name") or "default") == "default":
                    price_entry = entry
                    break
            if price_entry is None:
                price_entry = {"id": "default"}
                prices_meta.append(price_entry)
            price_entry["name"] = Path(path).name
            price_entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            if "auto_update" not in price_entry and metadata.get("auto_update"):
                price_entry["auto_update"] = metadata.get("auto_update")
            metadata["price_name"] = price_entry["name"]
            metadata["updated_at"] = price_entry["updated_at"]
            metadata["prices"] = prices_meta
            price_list.metadata = metadata
            self.repository.save(price_list)
            if save_template:
                self.template_store.save_template(supplier, column_mapping, headers)
            return price_list

        def on_success(price_list: PriceList) -> None:
            messagebox.showinfo(
                "Готово", f"Імпортовано {len(price_list.products)} позицій."
            )
            self.refresh_data()

        def on_error(exc: Exception) -> None:
            if isinstance(exc, MissingRequiredColumnsError):
                messagebox.showerror(
                    "Помилка імпорту",
                    "Не вдалося знайти обов'язкові колонки: {}.\nДоступні заголовки: {}.".format(
                        ", ".join(exc.missing), ", ".join(exc.headers)
                    ),
                )
            else:
                messagebox.showerror(
                    "Помилка імпорту", f"Не вдалося імпортувати прайс: {exc}"
                )

        self._run_background_task(
            title="Імпорт прайсу",
            message="Будь ласка, зачекайте. Триває імпорт прайс-листа…",
            task=task,
            on_success=on_success,
            on_error=on_error,
        )

    def _export_selected_prices(self) -> None:
        prices = self._selected_price_nodes()
        if not prices:
            messagebox.showwarning("Експорт", "Оберіть прайс у списку.")
            return
        if len(prices) > 1:
            messagebox.showinfo("Експорт", "Оберіть один прайс для експорту.")
            return

        meta = prices[0]
        supplier = meta.get("supplier")
        if not supplier:
            return
        price_list = self.price_lists.get(supplier)
        if not price_list:
            messagebox.showwarning("Експорт", "Прайс не знайдено.")
            return
        if not price_list.products:
            messagebox.showwarning("Експорт", "У прайсі немає товарів для експорту.")
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти як",
            defaultextension=".csv",
            filetypes=(
                ("CSV файл", "*.csv"),
                ("JSON файл", "*.json"),
                ("XML файл", "*.xml"),
                ("Excel файл", "*.xlsx"),
            ),
        )
        if not path:
            return

        try:
            self.exporter.export(price_list.products, path)
        except Exception as exc:
            messagebox.showerror("Помилка експорту", f"Не вдалося зберегти файл: {exc}")
            return

        messagebox.showinfo("Готово", "Дані успішно збережено.")

    def _delete_selected_prices(self) -> None:
        prices = self._selected_price_nodes()
        if not prices:
            supplier = self._selected_supplier_from_tree()
            if not supplier:
                messagebox.showwarning("Видалення", "Оберіть прайс у списку.")
                return
            if not messagebox.askyesno(
                "Видалення",
                "Видалити всі прайси постачальника '{}' ?".format(supplier),
            ):
                return
            try:
                self.repository.delete(supplier)
            except Exception as exc:
                messagebox.showerror("Помилка", f"Не вдалося видалити дані: {exc}")
                return
            self.refresh_data()
            return

        confirm_names = []
        for meta in prices:
            supplier = meta.get("supplier") or "?"
            price_name = meta.get("name") or "прайс"
            confirm_names.append(f"{price_name} ({supplier})")
        if not messagebox.askyesno(
            "Видалення",
            "Видалити прайси:\n - " + "\n - ".join(confirm_names) + "?",
        ):
            return

        for meta in prices:
            supplier = meta.get("supplier")
            price_id = meta.get("price_id") or "default"
            if not supplier:
                continue
            price_list = self.price_lists.get(supplier)
            if not price_list:
                continue
            metadata = dict(price_list.metadata or {})
            prices_meta = metadata.get("prices")
            if isinstance(prices_meta, list):
                metadata["prices"] = [
                    entry
                    for entry in prices_meta
                    if str(entry.get("id") or entry.get("name") or "default") != str(price_id)
                ]
            else:
                metadata["prices"] = []
            metadata.pop("price_name", None)
            metadata.pop("updated_at", None)
            price_list.metadata = metadata
            price_list.products = []
            try:
                self.repository.save(price_list)
            except Exception as exc:
                messagebox.showerror("Помилка", f"Не вдалося оновити дані: {exc}")
                return

        self.refresh_data()

    def _add_supplier(self) -> None:
        name = simpledialog.askstring("Новий постачальник", "Назва постачальника:")
        if not name:
            return
        name = name.strip()
        if not name:
            messagebox.showwarning("Постачальник", "Назва не може бути порожньою.")
            return
        if name in self.price_lists:
            messagebox.showwarning("Постачальник", "Такий постачальник вже існує.")
            return
        price_list = PriceList(supplier=name, products=[], metadata={"prices": []})
        try:
            self.repository.save(price_list)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося створити постачальника: {exc}")
            return
        self.refresh_data()

    def _remove_supplier(self) -> None:
        supplier = self._selected_supplier_from_tree()
        if not supplier:
            messagebox.showwarning("Видалення", "Оберіть постачальника у списку.")
            return
        if not messagebox.askyesno(
            "Видалення",
            f"Видалити постачальника '{supplier}' та всі його дані?",
        ):
            return
        try:
            self.repository.delete(supplier)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося видалити дані: {exc}")
            return
        self.refresh_data()

    def _rename_supplier(self) -> None:
        supplier = self._selected_supplier_from_tree()
        if not supplier:
            messagebox.showwarning("Перейменування", "Оберіть постачальника у списку.")
            return
        new_name = simpledialog.askstring(
            "Перейменувати постачальника",
            "Нова назва:",
            initialvalue=supplier,
        )
        if not new_name or new_name.strip() == supplier:
            return
        new_name = new_name.strip()
        if new_name in self.price_lists:
            messagebox.showwarning("Перейменування", "Постачальник з такою назвою вже існує.")
            return
        try:
            self.repository.rename(supplier, new_name)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося перейменувати: {exc}")
            return
        self.refresh_data()

    def _refresh_price_file(self) -> None:
        prices = self._selected_price_nodes()
        if not prices:
            messagebox.showwarning("Оновлення", "Оберіть прайс у списку.")
            return
        if len(prices) > 1:
            messagebox.showinfo("Оновлення", "Оберіть один прайс для оновлення.")
            return
        supplier = prices[0].get("supplier")
        if not supplier:
            return
        self._import_price_list(supplier=supplier)

    def _configure_auto_update(self) -> None:
        prices = self._selected_price_nodes()
        if not prices:
            messagebox.showwarning("Автооновлення", "Оберіть прайс у списку.")
            return
        if len(prices) > 1:
            messagebox.showinfo("Автооновлення", "Налаштувати можна лише один прайс за раз.")
            return
        meta = prices[0]
        supplier = meta.get("supplier")
        price_id = meta.get("price_id") or "default"
        if not supplier:
            return
        price_list = self.price_lists.get(supplier)
        if not price_list:
            return
        metadata = dict(price_list.metadata or {})
        prices_meta = metadata.get("prices")
        entry: Dict[str, object] | None = None
        if isinstance(prices_meta, list):
            for candidate in prices_meta:
                if not isinstance(candidate, dict):
                    continue
                if str(candidate.get("id") or candidate.get("name") or "default") == str(price_id):
                    entry = candidate
                    break
        if entry is None:
            entry = {"id": price_id}
            prices_meta = prices_meta if isinstance(prices_meta, list) else []
            prices_meta.append(entry)
            metadata["prices"] = prices_meta

        templates = self.template_store.list_templates()
        current_template = entry.get("auto_update", {}).get("template") if isinstance(entry.get("auto_update"), dict) else None

        dialog = AutoUpdateDialog(
            self,
            supplier=supplier,
            price_name=str(meta.get("name") or "Прайс"),
            templates=templates,
            current_template=current_template,
            initial_settings=entry.get("auto_update", {}),
        )
        self.wait_window(dialog)
        if not dialog.result:
            return

        settings = dialog.result.get("settings") or {}
        entry["auto_update"] = settings
        metadata["prices"] = prices_meta if isinstance(prices_meta, list) else [entry]
        price_list.metadata = metadata
        try:
            self.repository.save(price_list)
        except Exception as exc:
            messagebox.showerror(
                "Автооновлення", f"Не вдалося зберегти налаштування: {exc}"
            )
            return

        if dialog.result.get("action") == "run":
            messagebox.showinfo(
                "Автооновлення",
                "Запуск автооновлення наразі доступний після збереження налаштувань.",
            )

        self.refresh_data()

    def _load_tag_rules(self) -> None:
        path = filedialog.askopenfilename(
            title="Файл правил тегування",
            filetypes=(("JSON файл", "*.json"), ("Усі файли", "*.*")),
        )
        if not path:
            return

        try:
            self.tagger = Tagger.from_json(path)
            self._tags_config_path = path
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося завантажити правила: {exc}")
            return

        messagebox.showinfo(
            "Готово",
            "Правила тегування оновлено. Нові імпорти будуть автоматично використовувати ці налаштування.",
        )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    def _perform_search(self) -> None:
        query = self.search_query.get().strip()
        if not query:
            messagebox.showwarning("Пошук", "Введіть пошуковий запит.")
            return

        supplier_value = self.search_supplier.get()
        supplier = None if supplier_value in {"", "Усі"} else supplier_value

        fuzzy = self.fuzzy_var.get()
        threshold = float(self.threshold_var.get())
        limit = max(1, int(self.limit_var.get()))

        search = ProductSearch(self._all_price_lists())
        results = search.search(
            query,
            supplier=supplier,
            fuzzy=fuzzy,
            limit=limit,
            threshold=threshold,
        )

        self.search_results = [result.product for result in results]

        for item in self.search_tree.get_children():
            self.search_tree.delete(item)

        for result in results:
            product = result.product
            score = f"{result.score:.2f}" if fuzzy else "—"
            self.search_tree.insert(
                "",
                tk.END,
                values=(
                    product.sku,
                    product.name,
                    _format_price(product),
                    product.supplier or "",
                    score,
                ),
            )

    def _export_search_results(self) -> None:
        if not self.search_results:
            messagebox.showwarning("Експорт", "Немає результатів для експорту.")
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти результати",
            defaultextension=".csv",
            filetypes=(("CSV файл", "*.csv"), ("JSON файл", "*.json")),
        )
        if not path:
            return

        try:
            self.exporter.export(self.search_results, path)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося зберегти: {exc}")
            return

        messagebox.showinfo("Готово", "Результати експортовано.")

    def _import_store_price(self) -> None:
        path = filedialog.askopenfilename(
            title="Оберіть файл прайсу магазину",
            filetypes=(
                ("CSV файли", "*.csv"),
                ("JSON файли", "*.json"),
                ("XML файли", "*.xml"),
                ("Excel файли", "*.xlsx"),
                ("Усі підтримувані", "*.csv *.json *.xml *.xlsx"),
            ),
        )
        if not path:
            return

        supplier = STORE_SUPPLIER_NAME

        try:
            headers, preview_rows = self.importer.peek(path, limit=15)
        except Exception as exc:
            messagebox.showerror(
                "Помилка імпорту",
                f"Не вдалося проаналізувати файл: {exc}",
            )
            return

        template = self.template_store.get_template(supplier)
        available_templates = self.template_store.list_templates()

        suggested_mapping = self.importer.suggest_mapping(
            headers,
            column_mapping=template.column_mapping if template else None,
        )
        initial_mapping = dict(suggested_mapping)
        if template:
            for field, column in template.column_mapping.items():
                if column in headers:
                    initial_mapping[field] = column

        dialog = ImportSettingsDialog(
            self,
            path=path,
            supplier=supplier,
            headers=headers,
            preview_rows=preview_rows,
            required_fields=sorted(self.importer.required_fields),
            optional_fields=list(self.importer.optional_fields),
            initial_mapping=initial_mapping,
            templates=available_templates,
            current_template=template,
        )
        self.wait_window(dialog)

        if not dialog.result:
            return

        column_mapping = dialog.result["column_mapping"]
        save_template = dialog.result["save_template"]

        def task() -> PriceList:
            price_list = self.importer.load(
                path, supplier=supplier, column_mapping=column_mapping
            )
            self.tagger.apply(price_list.products)
            return price_list

        def on_success(price_list: PriceList) -> None:
            self.store_price_list = price_list
            try:
                self._save_store_price(price_list)
            except Exception as exc:
                messagebox.showerror(
                    "Помилка", f"Не вдалося зберегти прайс магазину: {exc}"
                )
                return

            if save_template:
                self.template_store.save_template(supplier, column_mapping, headers)

            messagebox.showinfo(
                "Готово",
                f"Завантажено {len(price_list.products)} позицій прайсу магазину.",
            )
            self._update_store_tree()
            self._mark_comparisons_stale()

        def on_error(exc: Exception) -> None:
            if isinstance(exc, MissingRequiredColumnsError):
                messagebox.showerror(
                    "Помилка імпорту",
                    "Не вдалося знайти обов'язкові колонки: {}.\nДоступні заголовки: {}.".format(
                        ", ".join(exc.missing), ", ".join(exc.headers)
                    ),
                )
            else:
                messagebox.showerror(
                    "Помилка імпорту", f"Не вдалося імпортувати прайс: {exc}"
                )

        self._run_background_task(
            title="Прайс магазину",
            message="Будь ласка, зачекайте. Триває оновлення прайсу магазину…",
            task=task,
            on_success=on_success,
            on_error=on_error,
        )

    def _load_store_price_from_disk(self) -> None:
        if not self._store_price_path.exists():
            self.store_price_list = None
            return

        try:
            with self._store_price_path.open("r", encoding="utf-8") as fp:
                payload = json.load(fp)
        except Exception as exc:
            messagebox.showerror(
                "Помилка",
                f"Не вдалося завантажити прайс магазину: {exc}",
            )
            self.store_price_list = None
            return

        products = [
            Product(
                sku=item["sku"],
                name=item["name"],
                price=item["price"],
                currency=item.get("currency", "USD"),
                description=item.get("description"),
                supplier=payload.get("supplier", STORE_SUPPLIER_NAME),
                tags=set(item.get("tags", [])),
                extra=item.get("extra", {}),
            )
            for item in payload.get("products", [])
        ]

        supplier = payload.get("supplier") or STORE_SUPPLIER_NAME
        self.store_price_list = PriceList(
            supplier=supplier,
            products=products,
            metadata=payload.get("metadata", {}),
        )

    def _save_store_price(self, price_list: PriceList) -> None:
        payload = {
            "supplier": price_list.supplier,
            "metadata": price_list.metadata,
            "products": [
                {
                    "sku": product.sku,
                    "name": product.name,
                    "price": product.price,
                    "currency": product.currency,
                    "description": product.description,
                    "tags": sorted(product.tags),
                    "extra": product.extra,
                }
                for product in price_list.products
            ],
        }

        with self._store_price_path.open("w", encoding="utf-8") as fp:
            json.dump(payload, fp, indent=2, ensure_ascii=False)

    def _update_store_tree(self) -> None:
        for item in self.store_tree.get_children():
            self.store_tree.delete(item)

        if not self.store_price_list:
            self.store_status_var.set("Прайс не завантажено.")
            return

        products = sorted(
            self.store_price_list.products, key=lambda product: (product.sku, product.price)
        )
        self.store_status_var.set(
            f"Завантажено {len(products)} позицій прайсу магазину."
        )

        for product in products:
            tags = ", ".join(sorted(product.tags))
            self.store_tree.insert(
                "",
                tk.END,
                values=(product.sku, product.name, _format_price(product), tags),
            )

    def _all_price_lists(self) -> List[PriceList]:
        lists = list(self.price_lists.values())
        if self.store_price_list:
            lists.append(self.store_price_list)
        return lists

    def _current_store_products(self) -> Sequence[Product]:
        if not self.store_price_list:
            return []
        return list(self.store_price_list.products)

    def _all_products_for_tags(self) -> List[Product]:
        products: List[Product] = []
        if self.store_price_list:
            products.extend(self.store_price_list.products)
        for price_list in self.price_lists.values():
            products.extend(price_list.products)
        return products

    def _on_tags_saved(self, assignments: Sequence[tags_assignment.TagAssignment]) -> None:
        suppliers_to_save: Dict[str, PriceList] = {}
        for assignment in assignments:
            product = assignment.product
            final_tags = assignment.all_effective_tags()
            product.tags = set(final_tags)
            supplier = product.supplier or STORE_SUPPLIER_NAME
            if self.store_price_list and supplier == self.store_price_list.supplier:
                suppliers_to_save[supplier] = self.store_price_list
            elif supplier in self.price_lists:
                suppliers_to_save[supplier] = self.price_lists[supplier]

        if self.store_price_list and self.store_price_list.supplier in suppliers_to_save:
            try:
                self._save_store_price(self.store_price_list)
            except Exception as exc:
                messagebox.showerror("Збереження тегів", f"Не вдалося оновити мітки магазину: {exc}")

        for supplier, price_list in suppliers_to_save.items():
            if self.store_price_list and supplier == self.store_price_list.supplier:
                continue
            try:
                self.repository.save(price_list)
            except Exception as exc:
                messagebox.showerror(
                    "Збереження тегів",
                    f"Не вдалося оновити мітки для '{supplier}': {exc}",
                )

        self._update_store_tree()
        self._show_supplier_products()
        if hasattr(self, "comparison_board"):
            self.comparison_board.refresh_view()
        self._mark_comparisons_stale()


class JsonEditorDialog(tk.Toplevel):
    """Simple JSON editor dialog used for templates and rules."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        path: Path,
        title: str,
        default_payload: Callable[[], Mapping[str, object]],
    ) -> None:
        super().__init__(master)
        self.title(title)
        self.path = Path(path)
        self.default_payload = default_payload
        self.result = False

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        text_frame = ttk.Frame(self)
        text_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.text = tk.Text(text_frame, wrap=tk.NONE, undo=True)
        self.text.grid(row=0, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.text.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        payload = self._load_content()
        self.text.insert("1.0", payload)

        button_frame = ttk.Frame(self)
        button_frame.grid(row=1, column=0, sticky=tk.E, padx=8, pady=(0, 8))

        ttk.Button(button_frame, text="Скасувати", command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Зберегти", command=self._on_save).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _load_content(self) -> str:
        if self.path.exists():
            try:
                return self.path.read_text(encoding="utf-8")
            except OSError:
                pass
        payload = self.default_payload()
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _on_save(self) -> None:
        content = self.text.get("1.0", tk.END).strip() or "{}"
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            messagebox.showerror("Мітки", f"Некоректний JSON: {exc}")
            return

        formatted = json.dumps(data, ensure_ascii=False, indent=2)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(formatted + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Мітки", f"Не вдалося зберегти файл: {exc}")
            return

        self.result = True
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = False
        self.destroy()


class ModelTemplatesDialog(tk.Toplevel):
    """Visual editor for hierarchical model templates."""

    def __init__(self, master: tk.Misc, *, manager: ModelTemplatesEditor) -> None:
        super().__init__(master)
        self.title("Шаблони моделей")
        self.manager = manager
        self.modified = False

        self.selected_category: Optional[str] = None
        self.selected_brand: Optional[str] = None
        self.selected_model: Optional[str] = None

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        container = ttk.Frame(self, padding=12)
        container.grid(row=0, column=0, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)
        container.rowconfigure(0, weight=1)

        left_column = ttk.Frame(container)
        left_column.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        left_column.columnconfigure(0, weight=1)
        left_column.rowconfigure(0, weight=1)
        left_column.rowconfigure(1, weight=1)

        categories_frame = ttk.Frame(left_column)
        categories_frame.grid(row=0, column=0, sticky="nsew")
        categories_frame.columnconfigure(0, weight=1)
        categories_frame.rowconfigure(1, weight=1)

        ttk.Label(categories_frame, text="Категорії пристроїв").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        self.category_list = tk.Listbox(
            categories_frame, exportselection=False, height=8
        )
        self.category_list.grid(row=1, column=0, sticky="nsew")
        category_scroll = ttk.Scrollbar(
            categories_frame, orient=tk.VERTICAL, command=self.category_list.yview
        )
        category_scroll.grid(row=1, column=1, sticky="ns")
        self.category_list.configure(yscrollcommand=category_scroll.set)

        category_buttons = ttk.Frame(categories_frame)
        category_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        category_buttons.columnconfigure(0, weight=1)
        self.category_label_var = tk.StringVar(value="Категорія: —")
        ttk.Label(category_buttons, textvariable=self.category_label_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(category_buttons, text="Додати", command=self._add_category).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(
            category_buttons, text="Перейменувати", command=self._rename_category
        ).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(
            category_buttons, text="Видалити", command=self._delete_category
        ).grid(row=0, column=3, padx=(8, 0))

        brands_frame = ttk.Frame(left_column)
        brands_frame.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        brands_frame.columnconfigure(0, weight=1)
        brands_frame.rowconfigure(1, weight=1)

        ttk.Label(brands_frame, text="Бренди").grid(row=0, column=0, columnspan=2, sticky="w")
        self.brand_list = tk.Listbox(brands_frame, exportselection=False, height=8)
        self.brand_list.grid(row=1, column=0, sticky="nsew")
        brand_scroll = ttk.Scrollbar(
            brands_frame, orient=tk.VERTICAL, command=self.brand_list.yview
        )
        brand_scroll.grid(row=1, column=1, sticky="ns")
        self.brand_list.configure(yscrollcommand=brand_scroll.set)

        brand_buttons = ttk.Frame(brands_frame)
        brand_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        brand_buttons.columnconfigure(0, weight=1)
        self.brand_label_var = tk.StringVar(value="Бренд: —")
        ttk.Label(brand_buttons, textvariable=self.brand_label_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(brand_buttons, text="Додати", command=self._add_brand).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(brand_buttons, text="Перейменувати", command=self._rename_brand).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(brand_buttons, text="Видалити", command=self._delete_brand).grid(
            row=0, column=3, padx=(8, 0)
        )

        models_frame = ttk.Frame(container)
        models_frame.grid(row=0, column=1, sticky="nsew")
        models_frame.columnconfigure(0, weight=1)
        models_frame.rowconfigure(1, weight=1)

        ttk.Label(models_frame, text="Моделі").grid(row=0, column=0, columnspan=2, sticky="w")
        self.model_list = tk.Listbox(models_frame, exportselection=False, height=18)
        self.model_list.grid(row=1, column=0, sticky="nsew")
        model_scroll = ttk.Scrollbar(
            models_frame, orient=tk.VERTICAL, command=self.model_list.yview
        )
        model_scroll.grid(row=1, column=1, sticky="ns")
        self.model_list.configure(yscrollcommand=model_scroll.set)

        model_buttons = ttk.Frame(models_frame)
        model_buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        model_buttons.columnconfigure(0, weight=1)
        self.model_label_var = tk.StringVar(value="Модель: —")
        ttk.Label(model_buttons, textvariable=self.model_label_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Button(model_buttons, text="Додати", command=self._add_model).grid(
            row=0, column=1, padx=(8, 0)
        )
        ttk.Button(model_buttons, text="Перейменувати", command=self._rename_model).grid(
            row=0, column=2, padx=(8, 0)
        )
        ttk.Button(model_buttons, text="Видалити", command=self._delete_model).grid(
            row=0, column=3, padx=(8, 0)
        )

        footer = ttk.Frame(self, padding=(12, 0, 12, 12))
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Закрити", command=self._on_close).grid(
            row=0, column=1, sticky="e"
        )

        self.category_list.bind("<<ListboxSelect>>", self._on_category_select)
        self.brand_list.bind("<<ListboxSelect>>", self._on_brand_select)
        self.model_list.bind("<<ListboxSelect>>", self._on_model_select)

        self._refresh_categories()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _on_close(self) -> None:
        self.grab_release()
        self.destroy()

    def _get_selected_value(self, widget: tk.Listbox) -> Optional[str]:
        selection = widget.curselection()
        if not selection:
            return None
        return widget.get(selection[0])

    def _select_value(
        self, widget: tk.Listbox, values: List[str], desired: Optional[str]
    ) -> Optional[str]:
        widget.selection_clear(0, tk.END)
        if desired and desired in values:
            index = values.index(desired)
        elif values:
            index = 0
            desired = values[0]
        else:
            return None
        widget.selection_set(index)
        widget.activate(index)
        widget.see(index)
        return desired

    def _refresh_categories(self, select: Optional[str] = None) -> None:
        values = self.manager.list_categories()
        self.category_list.delete(0, tk.END)
        for value in values:
            self.category_list.insert(tk.END, value)
        self.selected_category = self._select_value(
            self.category_list, values, select or self.selected_category
        )
        self._update_category_label()
        self._refresh_brands()

    def _refresh_brands(self, select: Optional[str] = None) -> None:
        if not self.selected_category:
            self.brand_list.delete(0, tk.END)
            self.selected_brand = None
            self._update_brand_label()
            self._refresh_models()
            return
        values = self.manager.list_brands(self.selected_category)
        self.brand_list.delete(0, tk.END)
        for value in values:
            self.brand_list.insert(tk.END, value)
        self.selected_brand = self._select_value(
            self.brand_list, values, select or self.selected_brand
        )
        self._update_brand_label()
        self._refresh_models()

    def _refresh_models(self, select: Optional[str] = None) -> None:
        if not (self.selected_category and self.selected_brand):
            self.model_list.delete(0, tk.END)
            self.selected_model = None
            self._update_model_label()
            return
        values = self.manager.list_models(self.selected_category, self.selected_brand)
        self.model_list.delete(0, tk.END)
        for value in values:
            self.model_list.insert(tk.END, value)
        self.selected_model = self._select_value(
            self.model_list, values, select or self.selected_model
        )
        self._update_model_label()

    def _update_category_label(self) -> None:
        value = self.selected_category or "—"
        self.category_label_var.set(f"Категорія: {value}")

    def _update_brand_label(self) -> None:
        value = self.selected_brand or "—"
        self.brand_label_var.set(f"Бренд: {value}")

    def _update_model_label(self) -> None:
        value = self.selected_model or "—"
        self.model_label_var.set(f"Модель: {value}")

    def _mark_modified(self) -> None:
        self.modified = True
        self.status_var.set("✅ Шаблони оновлено")

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------
    def _on_category_select(self, _: tk.Event[tk.Misc]) -> None:  # type: ignore[name-defined]
        selected = self._get_selected_value(self.category_list)
        if selected == self.selected_category:
            return
        self.selected_category = selected
        self._update_category_label()
        self._refresh_brands()

    def _on_brand_select(self, _: tk.Event[tk.Misc]) -> None:  # type: ignore[name-defined]
        selected = self._get_selected_value(self.brand_list)
        if selected == self.selected_brand:
            return
        self.selected_brand = selected
        self._update_brand_label()
        self._refresh_models()

    def _on_model_select(self, _: tk.Event[tk.Misc]) -> None:  # type: ignore[name-defined]
        selected = self._get_selected_value(self.model_list)
        if selected == self.selected_model:
            return
        self.selected_model = selected
        self._update_model_label()

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------
    def _add_category(self) -> None:
        name = simpledialog.askstring("Нова категорія", "Введіть назву категорії:", parent=self)
        if not name:
            return
        try:
            self.manager.add_category(name)
            self.manager.save_data()
        except ValueError as exc:
            messagebox.showerror("Категорії", str(exc), parent=self)
            return
        self._mark_modified()
        self._refresh_categories(select=name.strip())

    def _rename_category(self) -> None:
        if not self.selected_category:
            messagebox.showwarning("Категорії", "Оберіть категорію для перейменування.", parent=self)
            return
        new_name = simpledialog.askstring(
            "Перейменувати категорію",
            "Введіть нову назву:",
            initialvalue=self.selected_category,
            parent=self,
        )
        if not new_name or new_name.strip() == self.selected_category:
            return
        try:
            self.manager.rename_category(self.selected_category, new_name)
            self.manager.save_data()
        except ValueError as exc:
            messagebox.showerror("Категорії", str(exc), parent=self)
            return
        self._mark_modified()
        self.selected_category = new_name.strip()
        self._refresh_categories(select=self.selected_category)

    def _delete_category(self) -> None:
        if not self.selected_category:
            messagebox.showwarning("Категорії", "Оберіть категорію для видалення.", parent=self)
            return
        if not messagebox.askyesno(
            "Категорії",
            f"Видалити категорію '{self.selected_category}' разом з усіма брендами?",
            parent=self,
        ):
            return
        self.manager.delete_category(self.selected_category)
        self.manager.save_data()
        self._mark_modified()
        self.selected_category = None
        self._refresh_categories()

    def _add_brand(self) -> None:
        if not self.selected_category:
            messagebox.showwarning("Бренди", "Спочатку оберіть категорію.", parent=self)
            return
        name = simpledialog.askstring("Новий бренд", "Введіть назву бренду:", parent=self)
        if not name:
            return
        try:
            self.manager.add_brand(self.selected_category, name)
            self.manager.save_data()
        except ValueError as exc:
            messagebox.showerror("Бренди", str(exc), parent=self)
            return
        self._mark_modified()
        self.selected_brand = name.strip()
        self._refresh_brands(select=self.selected_brand)

    def _rename_brand(self) -> None:
        if not (self.selected_category and self.selected_brand):
            messagebox.showwarning("Бренди", "Оберіть бренд для перейменування.", parent=self)
            return
        new_name = simpledialog.askstring(
            "Перейменувати бренд",
            "Введіть нову назву:",
            initialvalue=self.selected_brand,
            parent=self,
        )
        if not new_name or new_name.strip() == self.selected_brand:
            return
        try:
            self.manager.rename_brand(self.selected_category, self.selected_brand, new_name)
            self.manager.save_data()
        except ValueError as exc:
            messagebox.showerror("Бренди", str(exc), parent=self)
            return
        self._mark_modified()
        self.selected_brand = new_name.strip()
        self._refresh_brands(select=self.selected_brand)

    def _delete_brand(self) -> None:
        if not (self.selected_category and self.selected_brand):
            messagebox.showwarning("Бренди", "Оберіть бренд для видалення.", parent=self)
            return
        if not messagebox.askyesno(
            "Бренди",
            f"Видалити бренд '{self.selected_brand}' разом з моделями?",
            parent=self,
        ):
            return
        self.manager.delete_brand(self.selected_category, self.selected_brand)
        self.manager.save_data()
        self._mark_modified()
        self.selected_brand = None
        self._refresh_brands()

    def _add_model(self) -> None:
        if not (self.selected_category and self.selected_brand):
            messagebox.showwarning("Моделі", "Оберіть бренд для додавання моделі.", parent=self)
            return
        raw_input = simpledialog.askstring(
            "Нова модель",
            "Введіть назву моделі або декілька через кому:",
            parent=self,
        )
        if not raw_input:
            return
        candidates = [part.strip() for part in raw_input.split(",") if part.strip()]
        if not candidates:
            return
        added: List[str] = []
        errors: List[str] = []
        for candidate in candidates:
            try:
                self.manager.add_model(
                    self.selected_category,
                    self.selected_brand,
                    candidate,
                )
                added.append(candidate)
            except ValueError as exc:
                errors.append(f"• {candidate}: {exc}")
        if not added:
            messagebox.showerror(
                "Моделі",
                "Не вдалося додати жодної моделі:\n" + "\n".join(errors),
                parent=self,
            )
            return
        self.manager.save_data()
        self._mark_modified()
        self.selected_model = added[-1]
        self._refresh_models(select=self.selected_model)
        if errors:
            messagebox.showwarning(
                "Моделі",
                "Деякі моделі не додано:\n" + "\n".join(errors),
                parent=self,
            )

    def _rename_model(self) -> None:
        if not (self.selected_category and self.selected_brand and self.selected_model):
            messagebox.showwarning("Моделі", "Оберіть модель для перейменування.", parent=self)
            return
        new_name = simpledialog.askstring(
            "Перейменувати модель",
            "Введіть нову назву:",
            initialvalue=self.selected_model,
            parent=self,
        )
        if not new_name or new_name.strip() == self.selected_model:
            return
        try:
            self.manager.rename_model(
                self.selected_category, self.selected_brand, self.selected_model, new_name
            )
            self.manager.save_data()
        except ValueError as exc:
            messagebox.showerror("Моделі", str(exc), parent=self)
            return
        self._mark_modified()
        self.selected_model = new_name.strip()
        self._refresh_models(select=self.selected_model)

    def _delete_model(self) -> None:
        if not (self.selected_category and self.selected_brand and self.selected_model):
            messagebox.showwarning("Моделі", "Оберіть модель для видалення.", parent=self)
            return
        if not messagebox.askyesno(
            "Моделі",
            f"Видалити модель '{self.selected_model}'?",
            parent=self,
        ):
            return
        self.manager.delete_model(
            self.selected_category, self.selected_brand, self.selected_model
        )
        self.manager.save_data()
        self._mark_modified()
        self.selected_model = None
        self._refresh_models()

_CONDITION_PATTERN = re.compile(
    r"REGEXMATCH\s*\(\s*\{\{\s*name\s*\}\}\s*,\s*(?P<literal>(\"(?:\\.|[^\"])*\")|('(?:\\.|[^'])*'))\s*\)",
    re.IGNORECASE,
)


def _escape_condition(condition: str) -> str:
    escaped = condition.replace("\\", "\\\\").replace("\"", r"\"")
    return escaped


def _conditions_to_formula(conditions: Sequence[str]) -> str:
    if not conditions:
        return ""
    parts = [
        f'REGEXMATCH({{{{name}}}},"{_escape_condition(condition)}")' for condition in conditions
    ]
    joined = ", ".join(parts)
    return f"=IF(AND({joined}))"


def _formula_to_conditions(formula: str) -> List[str]:
    conditions: List[str] = []
    for match in _CONDITION_PATTERN.finditer(formula or ""):
        literal = match.group("literal")
        try:
            value = ast.literal_eval(literal)
        except (SyntaxError, ValueError):
            value = literal[1:-1]
        if value:
            conditions.append(value)
    return conditions


class TagRuleRow:
    """Represents a single editable tag rule in the visual editor."""

    def __init__(
        self,
        editor: "TagRulesEditor",
        master: ttk.Frame,
        data: Mapping[str, object],
    ) -> None:
        self.editor = editor
        self.master = master
        self.name = str(data.get("name") or editor.generate_default_name())
        self.auto_confirm = bool(data.get("auto_confirm", True))
        self.tags: List[str] = [str(tag) for tag in data.get("tags", []) if str(tag).strip()]
        formula = str(data.get("formula") or "").strip()

        self.frame = ttk.Frame(master)
        self.frame.columnconfigure(0, weight=3)
        self.frame.columnconfigure(1, weight=2)

        self.condition_text = tk.Text(self.frame, height=3, width=60, wrap=tk.WORD)
        self.condition_text.insert("1.0", formula)
        self.condition_text.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        tags_wrapper = ttk.Frame(self.frame)
        tags_wrapper.grid(row=0, column=1, sticky="nw")

        self.tags_container = ttk.Frame(tags_wrapper)
        self.tags_container.pack(anchor="w")

        ttk.Button(
            tags_wrapper,
            text="+",
            width=3,
            command=self.prompt_add_tag,
        ).pack(anchor="w", pady=(4, 0))

        ttk.Button(
            self.frame,
            text="×",
            width=3,
            command=lambda: self.editor.remove_row(self),
        ).grid(row=0, column=2, sticky="ne")

        self.refresh_tags()

    def focus(self) -> None:
        self.condition_text.focus_set()

    def refresh_tags(self) -> None:
        for child in self.tags_container.winfo_children():
            child.destroy()

        if not self.tags:
            ttk.Label(self.tags_container, text="(Немає тегів)").pack(anchor="w", pady=2)
            return

        for tag in self.tags:
            pill = ttk.Frame(self.tags_container)
            pill.pack(side=tk.LEFT, padx=2, pady=2)

            tk.Label(
                pill,
                text=tag,
                bg="#e7f1ff",
                fg="#1a3d7c",
                padx=8,
                pady=2,
                bd=1,
                relief=tk.SOLID,
            ).pack(side=tk.LEFT)

            ttk.Button(
                pill,
                text="×",
                width=2,
                command=lambda value=tag: self.remove_tag(value),
            ).pack(side=tk.LEFT, padx=(2, 0))

    def prompt_add_tag(self) -> None:
        value = simpledialog.askstring("Мітки", "Нова мітка", parent=self.editor)
        if not value:
            return
        self.add_tag(value)

    def add_tag(self, tag: str) -> None:
        cleaned = tag.strip()
        if not cleaned:
            return
        if cleaned in self.tags:
            return
        self.tags.append(cleaned)
        self.refresh_tags()

    def remove_tag(self, tag: str) -> None:
        self.tags = [value for value in self.tags if value != tag]
        self.refresh_tags()

    def get_data(self) -> Dict[str, object]:
        formula = self.condition_text.get("1.0", tk.END).strip()
        return {
            "name": self.name,
            "formula": formula,
            "tags": list(self.tags),
            "auto_confirm": self.auto_confirm,
        }

    def destroy(self) -> None:
        self.frame.destroy()


class TagRulesEditor(tk.Toplevel):
    """Visual editor for tag rules with condition formulas and tag capsules."""

    def __init__(self, master: tk.Misc, *, path: Path) -> None:
        super().__init__(master)
        self.title("Правила тегування")
        self.path = Path(path)
        self.result = False

        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self.columnconfigure(0, weight=1)

        ttk.Label(
            self,
            text="Правила тегування",
            font=("TkDefaultFont", 12, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 4))

        header = ttk.Frame(self)
        header.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12)
        header.columnconfigure(0, weight=3)
        header.columnconfigure(1, weight=2)
        ttk.Label(header, text="Умова").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Мітки").grid(row=0, column=1, sticky="w")

        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.canvas.grid(row=2, column=0, sticky="nsew", padx=(12, 0), pady=(4, 12))
        self.rowconfigure(2, weight=1)

        scrollbar = ttk.Scrollbar(self, orient=tk.VERTICAL, command=self.canvas.yview)
        scrollbar.grid(row=2, column=1, sticky="ns", pady=(4, 12))
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.table_container = ttk.Frame(self.canvas)
        self.table_container.columnconfigure(0, weight=1)

        self._table_window = self.canvas.create_window(
            (0, 0), window=self.table_container, anchor="nw"
        )

        self.table_container.bind(
            "<Configure>",
            lambda event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._table_window, width=event.width),
        )

        self.rows: List[TagRuleRow] = []
        self._name_counter = 1

        for data in self._load_rules():
            self._add_row(data)

        quick_add = ttk.Frame(self)
        quick_add.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        quick_add.columnconfigure(0, weight=3)
        quick_add.columnconfigure(1, weight=2)

        ttk.Label(quick_add, text="Нова умова:").grid(row=0, column=0, sticky="w")
        ttk.Label(quick_add, text="Мітки (через кому):").grid(row=0, column=1, sticky="w")

        self.new_condition = tk.Text(quick_add, height=3, width=60, wrap=tk.WORD)
        self.new_condition.grid(row=1, column=0, sticky="ew", padx=(0, 8))

        self.new_tags_entry = ttk.Entry(quick_add)
        self.new_tags_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8))

        ttk.Button(quick_add, text="Додати", command=self._add_from_inputs).grid(
            row=1, column=2, sticky="e"
        )

        button_frame = ttk.Frame(self)
        button_frame.grid(row=4, column=0, columnspan=2, sticky="e", padx=12, pady=(0, 12))

        ttk.Button(button_frame, text="Скасувати", command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(button_frame, text="Зберегти", command=self._on_save).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def generate_default_name(self) -> str:
        name = f"Rule {self._name_counter}"
        self._name_counter += 1
        return name

    def _load_rules(self) -> List[Dict[str, object]]:
        if not self.path.exists():
            payload = tags_assignment.default_rules_payload()
        else:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = tags_assignment.default_rules_payload()

        rules_data: List[Dict[str, object]] = []
        for rule in payload.get("rules", []):
            conditions_value = rule.get("conditions", [])
            if isinstance(conditions_value, list):
                normalized_conditions = [str(cond) for cond in conditions_value if str(cond)]
            else:
                normalized_conditions = []

            formula = _conditions_to_formula(normalized_conditions)
            if not formula:
                condition_formula = str(rule.get("condition") or "").strip()
                if condition_formula:
                    formula = condition_formula
                    parsed = _formula_to_conditions(condition_formula)
                    if parsed:
                        normalized_conditions = parsed
            rules_data.append(
                {
                    "name": rule.get("name") or self.generate_default_name(),
                    "tags": [str(tag) for tag in rule.get("tags", []) if str(tag).strip()],
                    "formula": formula,
                    "auto_confirm": bool(rule.get("auto_confirm", True)),
                    }
            )
        if rules_data:
            self._name_counter = len(rules_data) + 1
        return rules_data

    def _add_row(self, data: Mapping[str, object]) -> TagRuleRow:
        row = TagRuleRow(self, self.table_container, data)
        self.rows.append(row)
        self._reflow_rows()
        return row

    def _reflow_rows(self) -> None:
        for index, row in enumerate(self.rows):
            row.frame.grid(row=index, column=0, sticky="ew", pady=4)

    def remove_row(self, row: TagRuleRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
            row.destroy()
            self._reflow_rows()

    def _add_from_inputs(self) -> None:
        condition = self.new_condition.get("1.0", tk.END).strip()
        tags_text = self.new_tags_entry.get().strip()

        if not condition:
            messagebox.showwarning("Правила", "Введіть формулу умови.", parent=self)
            return

        tags = [tag.strip() for tag in tags_text.split(",") if tag.strip()]
        if not tags:
            messagebox.showwarning("Правила", "Додайте принаймні одну мітку.", parent=self)
            return

        row = self._add_row({"formula": condition, "tags": tags, "auto_confirm": True})
        row.focus()

        self.new_condition.delete("1.0", tk.END)
        self.new_tags_entry.delete(0, tk.END)
        self.canvas.yview_moveto(1.0)

    def _collect_rows(self) -> List[Dict[str, object]]:
        collected: List[Dict[str, object]] = []
        for row in self.rows:
            data = row.get_data()
            formula = data.get("formula", "")
            tags = data.get("tags", [])
            if not formula and not tags:
                continue
            conditions = _formula_to_conditions(str(formula))
            if not conditions:
                messagebox.showerror(
                    "Правила",
                    "Кожне правило повинно містити щонайменше одну умову REGEXMATCH.",
                    parent=self,
                )
                return []
            if not tags:
                messagebox.showerror(
                    "Правила", "Кожне правило повинно містити хоча б одну мітку.", parent=self
                )
                return []
            collected.append(
                {
                    "name": data.get("name") or self.generate_default_name(),
                    "conditions": conditions,
                    "tags": list(tags),
                    "auto_confirm": bool(data.get("auto_confirm", True)),
                }
            )
        return collected

    def _on_save(self) -> None:
        payload = self._collect_rows()
        if not payload and self.rows:
            return

        data = {"rules": payload}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Правила", f"Не вдалося зберегти файл: {exc}", parent=self)
            return

        self.result = True
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = False
        self.destroy()

class TagSelectionDialog(tk.Toplevel):
    """Dialog for confirming or rejecting proposed tags for a product."""

    def __init__(self, master: tk.Misc, assignment: tags_assignment.TagAssignment) -> None:
        super().__init__(master)
        self.assignment = assignment
        self.result: Set[str] | None = None

        self.title("Підтвердження тегів")
        self.transient(master)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        ttk.Label(
            self,
            text=f"{assignment.product.sku or ''} — {assignment.product.name}",
            wraplength=520,
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=12, pady=(12, 4))

        ttk.Label(
            self,
            text="Поточні теги: "
            + (", ".join(sorted(assignment.current_tags)) or "—"),
            justify=tk.LEFT,
        ).pack(fill=tk.X, padx=12, pady=(0, 8))

        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=12)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)

        canvas = tk.Canvas(body, borderwidth=0, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        proposed = sorted(assignment.proposed_tags)
        self._tag_vars: Dict[str, tk.BooleanVar] = {}
        if proposed:
            for tag in proposed:
                var = tk.BooleanVar(value=tag in assignment.selected_tags)
                self._tag_vars[tag] = var
                ttk.Checkbutton(inner, text=tag, variable=var).pack(
                    anchor=tk.W, pady=2
                )
        else:
            ttk.Label(inner, text="Немає запропонованих тегів.").pack(anchor=tk.W, pady=4)

        inner.update_idletasks()

        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, padx=12, pady=(8, 12))

        ttk.Button(controls, text="Очистити", command=self._clear).pack(side=tk.LEFT)
        ttk.Button(controls, text="Обрати всі", command=self._select_all).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(controls, text="Скасувати", command=self._on_cancel).pack(side=tk.RIGHT)
        ttk.Button(controls, text="Готово", command=self._on_save).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

    def _clear(self) -> None:
        for var in self._tag_vars.values():
            var.set(False)

    def _select_all(self) -> None:
        for var in self._tag_vars.values():
            var.set(True)

    def _on_save(self) -> None:
        if not self._tag_vars:
            self.result = set()
        else:
            self.result = {tag for tag, var in self._tag_vars.items() if var.get()}
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class TagsTab(ttk.Frame):
    """Interactive tab for assigning tags to store and supplier products."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        data_dir: str | Path,
        synonyms_path: str | Path | None = None,
        get_products: Callable[[], Sequence[Product]],
        on_save: Callable[[Sequence[tags_assignment.TagAssignment]], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.data_dir = Path(data_dir)
        self.synonyms_path = Path(synonyms_path) if synonyms_path else self.data_dir / "synonyms.json"
        self.get_products = get_products
        self.on_save = on_save

        self.models_path = self.data_dir / "models.json"
        self.rules_path = self.data_dir / "tag_rules.json"
        self.assignments_path = self.data_dir / "tag_assignments.json"
        self._synonyms_manager: SynonymsManager | None = None

        self.templates = tags_assignment.load_tag_templates(self.models_path)
        self.rules = tags_assignment.load_tag_rules(self.rules_path)
        self.saved_map = tags_assignment.load_saved_tags(self.assignments_path)

        self.products: List[Product] = []
        self.assignments: Dict[str, tags_assignment.TagAssignment] = {}

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._update_tree())

        self._item_to_key: Dict[str, str] = {}

        self._build_ui()

    def invalidate_synonyms_cache(self) -> None:
        self._synonyms_manager = None

    def _get_synonyms_manager(self) -> SynonymsManager:
        if self._synonyms_manager is None:
            from .synonyms_manager import SynonymsManager  # Local import to avoid GUI dependency at module load

            self._synonyms_manager = SynonymsManager(self.synonyms_path)
        else:
            self._synonyms_manager.synonyms = self._synonyms_manager.load_synonyms()
        return self._synonyms_manager

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh_products(self, products: Sequence[Product] | None = None) -> None:
        products = list(products or self.get_products())
        self.products = products
        self.saved_map = tags_assignment.load_saved_tags(self.assignments_path)

        assignments: Dict[str, tags_assignment.TagAssignment] = {}
        for product in products:
            key = tags_assignment.make_assignment_key(
                product.supplier, product.sku, product.name
            )
            current_tags = set(product.tags)
            if key in self.saved_map:
                current_tags.update(self.saved_map[key])
            assignments[key] = tags_assignment.TagAssignment(
                product=product,
                current_tags=current_tags,
            )

        tags_assignment.validate_tags(assignments.values(), self.templates)
        self.assignments = assignments
        self._update_tree()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(controls, text="Пошук:").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        search_entry = ttk.Entry(controls, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12))
        controls.columnconfigure(1, weight=1)

        ttk.Button(
            controls,
            text="Автоматично присвоїти теги",
            command=self._run_auto_tagging,
        ).grid(row=0, column=2, sticky=tk.W, padx=(0, 8))
        ttk.Button(
            controls,
            text="Оновити мітки",
            command=lambda: self._run_auto_tagging(reload_saved=True),
        ).grid(row=0, column=3, sticky=tk.W)

        ttk.Button(
            controls,
            text="Редагувати шаблони моделей",
            command=self._edit_templates,
        ).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        ttk.Button(
            controls,
            text="Редагувати правила тегування",
            command=self._edit_rules,
        ).grid(row=1, column=2, columnspan=2, sticky=tk.W, pady=(8, 0))

        ttk.Button(
            controls,
            text="Масово застосувати обрані теги",
            command=self._apply_to_selected,
        ).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        ttk.Button(
            controls,
            text="Зберегти теги",
            command=self._save_assignments,
        ).grid(row=2, column=2, columnspan=2, sticky=tk.E, pady=(8, 0))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        columns = ("sku", "name", "current", "proposed", "category", "apply")
        self.tree = ttk.Treeview(
            tree_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
        )
        self.tree.grid(row=0, column=0, sticky="nsew")

        y_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(tree_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        headers = {
            "sku": "SKU",
            "name": "Назва",
            "current": "Поточні теги",
            "proposed": "Пропоновані теги",
            "category": "Категорія",
            "apply": "✔",
        }
        widths = {
            "sku": 130,
            "name": 320,
            "current": 220,
            "proposed": 260,
            "category": 140,
            "apply": 40,
        }
        for column in columns:
            self.tree.heading(column, text=headers[column])
            self.tree.column(column, width=widths[column], anchor=tk.W)

        self.tree.tag_configure("confirmed", background="#9bd179")
        self.tree.tag_configure("pending", background="#f5e9a4")
        self.tree.tag_configure("error", background="#d1a3a7")

        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-1>", self._on_tree_click, add="+")

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _run_auto_tagging(self, reload_saved: bool = False) -> None:
        if not self.products:
            self.refresh_products()
        if reload_saved:
            self.saved_map = tags_assignment.load_saved_tags(self.assignments_path)

        if not self.products:
            messagebox.showinfo("Мітки", "Немає продуктів для обробки.")
            return

        synonyms_manager = self._get_synonyms_manager()
        computed = tags_assignment.apply_tags_to_products(
            self.products,
            self.templates,
            self.rules,
            self.saved_map,
            synonyms_manager=synonyms_manager,
        )
        updated: Dict[str, tags_assignment.TagAssignment] = {}
        for assignment in computed:
            key = tags_assignment.make_assignment_key(
                assignment.product.supplier,
                assignment.product.sku,
                assignment.product.name,
            )
            existing = self.assignments.get(key)
            if existing:
                assignment.current_tags = set(existing.current_tags)
                preserved = {
                    tag for tag in existing.selected_tags if tag in assignment.proposed_tags
                }
                assignment.selected_tags |= preserved
            updated[key] = assignment

        self.assignments = updated
        tags_assignment.validate_tags(self.assignments.values(), self.templates)
        self._update_tree()

    def _apply_to_selected(self) -> None:
        changed = False
        for item in self.tree.selection():
            key = self._item_to_key.get(item)
            if not key:
                continue
            assignment = self.assignments.get(key)
            if not assignment:
                continue
            auto_pending = assignment.auto_tags & assignment.proposed_tags
            if not auto_pending:
                continue
            before = set(assignment.selected_tags)
            assignment.selected_tags.update(auto_pending)
            if assignment.selected_tags != before:
                changed = True

        if changed:
            tags_assignment.validate_tags(self.assignments.values(), self.templates)
            self._update_tree()

    def _save_assignments(self) -> None:
        assignments = list(self.assignments.values())
        for assignment in assignments:
            if assignment.selected_tags:
                assignment.current_tags.update(assignment.selected_tags)
                assignment.proposed_tags.difference_update(assignment.selected_tags)
                assignment.selected_tags.clear()

        tags_assignment.validate_tags(assignments, self.templates)

        try:
            tags_assignment.save_tags(self.assignments_path, assignments)
        except Exception as exc:
            messagebox.showerror("Мітки", f"Не вдалося зберегти теги: {exc}")
            return

        self.saved_map = tags_assignment.load_saved_tags(self.assignments_path)
        if self.on_save:
            self.on_save(assignments)

        self._update_tree()
        messagebox.showinfo("Мітки", "Теги успішно збережено.")

    def _edit_templates(self) -> None:
        manager = ModelTemplatesEditor(self.models_path)
        dialog = ModelTemplatesDialog(self, manager=manager)
        self.wait_window(dialog)
        if dialog.modified:
            self.templates = tags_assignment.load_tag_templates(self.models_path)
            messagebox.showinfo(
                "Мітки",
                "✅ Шаблони оновлено. Натисніть 'Оновити мітки' для перерахунку.",
            )

    def _edit_rules(self) -> None:
        dialog = TagRulesEditor(self, path=self.rules_path)
        self.wait_window(dialog)
        if dialog.result:
            self.rules = tags_assignment.load_tag_rules(self.rules_path)
            messagebox.showinfo(
                "Мітки",
                "Правила оновлено. Натисніть 'Оновити мітки' для перерахунку.",
            )

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------
    def _update_tree(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._item_to_key.clear()

        query = self.search_var.get().strip().lower()

        sorted_items = sorted(
            self.assignments.items(),
            key=lambda item: (
                (item[1].product.name or "").lower(),
                item[1].product.sku or "",
            ),
        )

        for key, assignment in sorted_items:
            haystack = " ".join(
                filter(
                    None,
                    [
                        assignment.product.sku,
                        assignment.product.name,
                        " ".join(sorted(assignment.current_tags)),
                        " ".join(sorted(assignment.proposed_tags)),
                    ],
                )
            ).lower()
            if query and query not in haystack:
                continue

            current_display = ", ".join(sorted(assignment.current_tags)) or "—"

            proposed_display: List[str] = []
            for tag in sorted(assignment.proposed_tags):
                markers: List[str] = []
                if tag in assignment.selected_tags:
                    markers.append("✔")
                origins: List[str] = []
                if tag in assignment.auto_tags:
                    origins.append("auto")
                if tag in assignment.rule_tags:
                    origins.append("rule")
                suffix = f" ({', '.join(origins)})" if origins else ""
                prefix = " ".join(markers) + (" " if markers else "")
                proposed_display.append(f"{prefix}{tag}{suffix}".strip())

            if assignment.validation_errors:
                proposed_display.append("⚠ " + "; ".join(assignment.validation_errors))

            proposed_column = ", ".join(proposed_display) if proposed_display else "—"

            category = assignment.category or "—"
            apply_marker = "✔" if assignment.selected_tags else ""

            item_id = self.tree.insert(
                "",
                tk.END,
                values=(
                    assignment.product.sku or "",
                    assignment.product.name,
                    current_display,
                    proposed_column,
                    category,
                    apply_marker,
                ),
            )

            row_tags: List[str] = []
            if assignment.validation_errors:
                row_tags.append("error")
            elif assignment.selected_tags:
                row_tags.append("confirmed")
            elif assignment.proposed_tags:
                row_tags.append("pending")
            elif assignment.current_tags:
                row_tags.append("confirmed")

            if row_tags:
                self.tree.item(item_id, tags=tuple(row_tags))

            self._item_to_key[item_id] = key

    def _on_tree_double_click(self, event: tk.Event[tk.Misc]) -> None:  # type: ignore[name-defined]
        item = self.tree.identify_row(event.y)
        if not item:
            return
        key = self._item_to_key.get(item)
        if not key:
            return
        assignment = self.assignments.get(key)
        if not assignment or not assignment.proposed_tags:
            return
        dialog = TagSelectionDialog(self, assignment)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        assignment.selected_tags = set(dialog.result)
        tags_assignment.validate_tags([assignment], self.templates)
        self._update_tree()

    def _on_tree_click(self, event: tk.Event[tk.Misc]) -> str | None:  # type: ignore[name-defined]
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return None
        column = self.tree.identify_column(event.x)
        if column != "#6":
            return None
        item = self.tree.identify_row(event.y)
        if not item:
            return None
        self.tree.selection_set(item)
        self._toggle_apply(item)
        return "break"

    def _toggle_apply(self, item: str) -> None:
        key = self._item_to_key.get(item)
        if not key:
            return
        assignment = self.assignments.get(key)
        if not assignment:
            return

        auto_pending = assignment.auto_tags & assignment.proposed_tags
        if not auto_pending:
            auto_pending = set(assignment.proposed_tags)
        if not auto_pending:
            return

        if auto_pending <= assignment.selected_tags:
            assignment.selected_tags.difference_update(auto_pending)
        else:
            assignment.selected_tags.update(auto_pending)

        tags_assignment.validate_tags([assignment], self.templates)
        self._update_tree()

@dataclass
class ComparisonRow:
    row_id: str
    group_id: str
    group_type: str
    store_product: Product | None
    supplier_match: SupplierMatch | None
    similarity: float
    status: str
    group_has_matches: bool
    match_id: str | None

    @property
    def supplier_product(self) -> Product | None:
        return self.supplier_match.product if self.supplier_match else None

    @property
    def data_sku(self) -> str:
        if self.supplier_match and self.supplier_match.product.sku:
            return self.supplier_match.product.sku
        if self.store_product and self.store_product.sku:
            return self.store_product.sku
        return ""


class ComparisonBoard(ttk.Frame):
    """Interactive table for comparing store products against suppliers."""

    FILTER_OPTIONS = (
        ("all", "Усі товари"),
        ("has_matches", "Є збіги"),
        ("no_matches", "Немає збігів"),
        ("supplier_only", "Лише постачальники"),
        ("confirmed", "Підтверджені"),
        ("pending", "Непідтверджені"),
    )

    SORT_OPTIONS = (
        ("name", "Назва (мій товар)"),
        ("sku", "SKU (мій товар)"),
        ("price", "Ціна (мій товар)"),
        ("supplier_name", "Назва (постачальник)"),
        ("supplier_price", "Ціна (постачальник)"),
    )

    def __init__(
        self,
        master: tk.Misc,
        *,
        get_store_products: Callable[[], Sequence[Product]],
        get_supplier_lists: Callable[[], Sequence[PriceList]],
        state_store: ComparisonStateStore,
        on_reload: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        self.get_store_products = get_store_products
        self.get_supplier_lists = get_supplier_lists
        self.state_store = state_store
        self.on_reload = on_reload

        self.min_similarity = 0.1
        self.low_similarity_threshold = 0.45

        self._ordered_rows: List[ComparisonRow] = []
        self._rows: Dict[str, ComparisonRow] = {}
        self._selection: Dict[str, bool] = {}
        self._item_to_row: Dict[str, str] = {}
        self._checkbox_meta: Dict[str, str] = {}

        self.search_var = tk.StringVar()
        self.threshold_var = tk.DoubleVar(value=0.6)
        self.filter_var = tk.StringVar(value="all")
        self.sort_var = tk.StringVar(value="name")

        self._filter_label_to_key = {label: key for key, label in self.FILTER_OPTIONS}
        self._sort_label_to_key = {label: key for key, label in self.SORT_OPTIONS}

        self.filter_label_var = tk.StringVar(value=self._label_for_filter("all"))
        self.sort_label_var = tk.StringVar(value=self._label_for_sort("name"))
        self.threshold_display_var = tk.StringVar(value="60%")
        self.status_var = tk.StringVar(value="")
        self._busy = False
        self._stale = False
        self._action_buttons: List[ttk.Button] = []

        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def refresh(self, rows: Sequence[ComparisonRow] | None = None) -> None:
        previous_selection = {row_id for row_id, selected in self._selection.items() if selected}
        computed_rows = list(rows) if rows is not None else self._rebuild_rows()
        self._ordered_rows = computed_rows
        self._rows = {row.row_id: row for row in self._ordered_rows}
        self._selection = {row.row_id: (row.row_id in previous_selection) for row in self._ordered_rows}
        self._apply_filters()
        self.clear_stale()

    def refresh_view(self) -> None:
        self._apply_filters()

    def build_rows(self) -> List[ComparisonRow]:
        return self._rebuild_rows()

    def mark_stale(self) -> None:
        self._stale = True
        self._update_status_message()

    def clear_stale(self) -> None:
        self._stale = False
        self._update_status_message()

    def set_busy(self, busy: bool) -> None:
        if self._busy == busy:
            self._update_status_message()
            return
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        for button in self._action_buttons:
            button.config(state=state)
        try:
            if busy:
                self.tree.state(["disabled"])
            else:
                self.tree.state(["!disabled"])
        except tk.TclError:
            pass
        self._update_status_message()

    def _set_status(self, message: str, *, severity: str = "info") -> None:
        colors = {
            "info": "#1a3d7c",
            "warning": "#a15c13",
            "error": "#a94442",
        }
        if message:
            self.status_var.set(message)
            self.status_label.configure(foreground=colors.get(severity, "#1a3d7c"))
            if not self.status_label.winfo_ismapped():
                self.status_label.pack(fill=tk.X, padx=8, pady=(0, 4))
        else:
            self.status_var.set("")
            if self.status_label.winfo_ismapped():
                self.status_label.pack_forget()

    def _update_status_message(self) -> None:
        if self._busy:
            self._set_status("Виконується оновлення співставлень…", severity="info")
        elif self._stale:
            self._set_status(
                "Дані співставлення застаріли. Натисніть «Оновити зв'язки».",
                severity="warning",
            )
        else:
            self._set_status("")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        controls = ttk.Frame(self)
        controls.pack(fill=tk.X, padx=8, pady=(8, 4))

        ttk.Label(controls, text="Пошук (назва/SKU):").grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        search_entry = ttk.Entry(controls, textvariable=self.search_var)
        search_entry.grid(row=0, column=1, sticky=tk.EW, padx=(0, 12))
        self.search_var.trace_add("write", lambda *_: self._apply_filters())

        ttk.Label(controls, text="Поріг схожості:").grid(row=0, column=2, sticky=tk.W, padx=(0, 6))
        threshold_scale = ttk.Scale(
            controls,
            from_=0.0,
            to=1.0,
            orient=tk.HORIZONTAL,
            variable=self.threshold_var,
            command=self._on_threshold_change,
        )
        threshold_scale.grid(row=0, column=3, sticky=tk.EW, padx=(0, 4))
        ttk.Label(controls, textvariable=self.threshold_display_var).grid(
            row=0, column=4, sticky=tk.W
        )

        ttk.Label(controls, text="Фільтр:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        filter_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=list(self._filter_label_to_key.keys()),
            textvariable=self.filter_label_var,
            width=28,
        )
        filter_combo.grid(row=1, column=1, sticky=tk.W, pady=(8, 0))
        filter_combo.bind("<<ComboboxSelected>>", self._on_filter_selected)

        ttk.Label(controls, text="Сортування:").grid(row=1, column=2, sticky=tk.W, padx=(0, 6), pady=(8, 0))
        sort_combo = ttk.Combobox(
            controls,
            state="readonly",
            values=list(self._sort_label_to_key.keys()),
            textvariable=self.sort_label_var,
            width=28,
        )
        sort_combo.grid(row=1, column=3, sticky=tk.W, pady=(8, 0))
        sort_combo.bind("<<ComboboxSelected>>", self._on_sort_selected)

        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)

        actions = ttk.Frame(self)
        actions.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.confirm_button = ttk.Button(
            actions, text="Підтвердити вибрані аналоги", command=self._confirm_selected
        )
        self.confirm_button.pack(side=tk.LEFT)
        self.remove_button = ttk.Button(
            actions, text="Видалити вибрані", command=self._remove_selected
        )
        self.remove_button.pack(side=tk.LEFT, padx=(8, 0))
        self.reload_button = ttk.Button(
            actions, text="Оновити результати", command=self._on_reload_clicked
        )
        self.reload_button.pack(side=tk.LEFT, padx=(8, 0))
        self.focus_button = ttk.Button(
            actions,
            text="Показати тільки непідтверджені",
            command=self._focus_unconfirmed,
        )
        self.focus_button.pack(side=tk.LEFT, padx=(8, 0))
        self._action_buttons = [
            self.confirm_button,
            self.remove_button,
            self.reload_button,
            self.focus_button,
        ]

        self.status_label = ttk.Label(self, textvariable=self.status_var, anchor=tk.W)
        self.status_label.pack(fill=tk.X, padx=8, pady=(0, 4))
        self.status_label.pack_forget()

        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        columns = (
            "my_sku",
            "my_name",
            "my_tags",
            "my_price",
            "supplier",
            "supplier_name",
            "supplier_tags",
            "supplier_price",
            "similarity",
            "checkbox",
        )
        self.tree = ttk.Treeview(
            table_frame,
            columns=columns,
            show="headings",
            selectmode="none",
        )

        headings = {
            "my_sku": "Мій SKU",
            "my_name": "Назва (мій товар)",
            "my_tags": "Мітки",
            "my_price": "Ціна",
            "supplier": "Постачальник",
            "supplier_name": "Назва (постачальник)",
            "supplier_tags": "Мітки",
            "supplier_price": "Ціна",
            "similarity": "Схожість",
            "checkbox": "✔",
        }
        widths = {
            "my_sku": 140,
            "my_name": 280,
            "my_tags": 180,
            "my_price": 120,
            "supplier": 160,
            "supplier_name": 280,
            "supplier_tags": 180,
            "supplier_price": 120,
            "similarity": 90,
            "checkbox": 80,
        }
        anchors = {
            "my_price": tk.E,
            "supplier_price": tk.E,
            "similarity": tk.CENTER,
            "checkbox": tk.CENTER,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(
                column,
                width=widths[column],
                anchor=anchors.get(column, tk.W),
                stretch=(column != "checkbox"),
            )

        y_scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        x_scroll = ttk.Scrollbar(table_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        self.tree.tag_configure("match-full", background="#9bd179")
        self.tree.tag_configure("match-low", background="#d1a3a7")
        self.tree.tag_configure("match-none", background="")

        self.tree.bind("<Button-1>", self._on_tree_click)

    # ------------------------------------------------------------------
    # Data preparation
    # ------------------------------------------------------------------
    def _rebuild_rows(self) -> List[ComparisonRow]:
        store_products = list(self.get_store_products())
        supplier_lists = list(self.get_supplier_lists())

        builder = ComparisonBuilder(
            store_products,
            supplier_lists,
            min_similarity=self.min_similarity,
        )
        groups = self._sort_groups(builder.build())

        rows: List[ComparisonRow] = []
        valid_pairs: List[tuple[str, str]] = []
        for group in groups:
            group_type = "supplier-only" if group.store_product is None else "store"
            if group.supplier_matches:
                for match in group.supplier_matches:
                    valid_pairs.append((group.group_id, match.match_id))
                    status = self.state_store.status_for(group.group_id, match.match_id)
                    if status == "removed":
                        continue
                    if status is None and match.similarity >= 0.999:
                        status = "confirmed"
                        self.state_store.set_status(group.group_id, match.match_id, "confirmed")
                    status = status or "pending"
                    rows.append(
                        ComparisonRow(
                            row_id=f"{group.group_id}::{match.match_id}",
                            group_id=group.group_id,
                            group_type=group_type,
                            store_product=group.store_product,
                            supplier_match=match,
                            similarity=match.similarity,
                            status=status,
                            group_has_matches=True,
                            match_id=match.match_id,
                        )
                    )
            else:
                if group_type == "store":
                    rows.append(
                        ComparisonRow(
                            row_id=f"{group.group_id}::store",
                            group_id=group.group_id,
                            group_type=group_type,
                            store_product=group.store_product,
                            supplier_match=None,
                            similarity=0.0,
                            status="pending",
                            group_has_matches=False,
                            match_id=None,
                        )
                    )

        self.state_store.prune(valid_pairs)
        self.state_store.save()
        return rows

    def _sort_groups(self, groups: List[ComparisonGroup]) -> List[ComparisonGroup]:
        key = self.sort_var.get()

        def store_name(group: ComparisonGroup) -> str:
            if group.store_product:
                return group.store_product.name.lower()
            if group.supplier_matches:
                return group.supplier_matches[0].product.name.lower()
            return ""

        def store_sku(group: ComparisonGroup) -> str:
            if group.store_product and group.store_product.sku:
                return group.store_product.sku.lower()
            if group.supplier_matches and group.supplier_matches[0].product.sku:
                return group.supplier_matches[0].product.sku.lower()
            return ""

        def supplier_name(group: ComparisonGroup) -> str:
            if group.supplier_matches:
                return group.supplier_matches[0].product.name.lower()
            return store_name(group)

        def store_price_value(group: ComparisonGroup) -> float:
            if group.store_product:
                return group.store_product.price
            return float("inf")

        def supplier_price_value(group: ComparisonGroup) -> float:
            if group.supplier_matches:
                return min(match.product.price for match in group.supplier_matches)
            return float("inf")

        if key == "sku":
            return sorted(groups, key=lambda group: (store_sku(group), store_name(group)))
        if key == "price":
            return sorted(groups, key=lambda group: (store_price_value(group), store_name(group)))
        if key == "supplier_name":
            return sorted(groups, key=lambda group: supplier_name(group))
        if key == "supplier_price":
            return sorted(
                groups,
                key=lambda group: (supplier_price_value(group), supplier_name(group)),
            )
        return sorted(groups, key=lambda group: store_name(group))

    # ------------------------------------------------------------------
    # Filtering helpers
    # ------------------------------------------------------------------
    def _apply_filters(self) -> None:
        filtered = [row for row in self._ordered_rows if self._passes_filters(row)]
        self._populate_tree(filtered)

    def _passes_filters(self, row: ComparisonRow) -> bool:
        return (
            self._passes_filter_option(row)
            and self._passes_search(row)
            and self._passes_threshold(row)
        )

    def _passes_filter_option(self, row: ComparisonRow) -> bool:
        option = self.filter_var.get()
        if option == "all":
            return True
        if option == "has_matches":
            return row.group_type == "store" and row.group_has_matches
        if option == "no_matches":
            return row.group_type == "store" and not row.group_has_matches
        if option == "supplier_only":
            return row.group_type == "supplier-only"
        if option == "confirmed":
            return row.status == "confirmed"
        if option == "pending":
            return row.status not in {"confirmed", "removed"}
        return True

    def _passes_search(self, row: ComparisonRow) -> bool:
        query = self.search_var.get().strip().lower()
        if not query:
            return True

        haystacks: List[str] = []
        if row.store_product:
            haystacks.extend(
                filter(
                    None,
                    [
                        row.store_product.sku,
                        row.store_product.name,
                        " ".join(sorted(row.store_product.tags)),
                    ],
                )
            )
        supplier_product = row.supplier_product
        if supplier_product:
            haystacks.extend(
                filter(
                    None,
                    [
                        supplier_product.sku,
                        supplier_product.name,
                        supplier_product.supplier,
                        " ".join(sorted(supplier_product.tags)),
                    ],
                )
            )
        return any(query in value.lower() for value in haystacks)

    def _passes_threshold(self, row: ComparisonRow) -> bool:
        if not row.supplier_match:
            return True
        threshold = float(self.threshold_var.get())
        return row.similarity >= threshold

    # ------------------------------------------------------------------
    # Tree rendering
    # ------------------------------------------------------------------
    def _populate_tree(self, rows: Sequence[ComparisonRow]) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        self._item_to_row.clear()
        self._checkbox_meta.clear()

        for row in rows:
            values = self._row_values(row)
            item = self.tree.insert("", tk.END, values=values, tags=self._row_tags(row))
            self._item_to_row[item] = row.row_id
            self._checkbox_meta[row.row_id] = row.data_sku
            checkbox_symbol = "☑" if self._selection.get(row.row_id) else "☐"
            self.tree.set(item, "checkbox", checkbox_symbol)

    def _row_values(self, row: ComparisonRow) -> tuple[str, ...]:
        store_product = row.store_product
        supplier_product = row.supplier_product

        my_sku = store_product.sku if store_product else "—"
        my_name = store_product.name if store_product else "—"
        my_tags = ", ".join(sorted(store_product.tags)) if store_product else ""
        my_price = _format_price(store_product) if store_product else ""

        supplier_name = supplier_product.name if supplier_product else "—"
        supplier_tags = ", ".join(sorted(supplier_product.tags)) if supplier_product else ""
        supplier_price = _format_price(supplier_product) if supplier_product else ""

        similarity = f"{row.similarity * 100:.0f}%" if row.supplier_match else "—"
        checkbox_symbol = "☑" if self._selection.get(row.row_id) else "☐"

        return (
            my_sku,
            my_name,
            my_tags,
            my_price,
            supplier_product.supplier if supplier_product else "—",
            supplier_name,
            supplier_tags,
            supplier_price,
            similarity,
            checkbox_symbol,
        )

    def _row_tags(self, row: ComparisonRow) -> tuple[str, ...]:
        sku_tag = f"data-sku::{row.data_sku}" if row.data_sku else "data-sku::"
        if row.status == "confirmed":
            return ("match-full", "compare-checkbox", sku_tag)
        if row.status == "flagged" or (
            row.supplier_match and row.similarity < self.low_similarity_threshold
        ):
            return ("match-low", "compare-checkbox", sku_tag)
        return ("match-none", "compare-checkbox", sku_tag)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_tree_click(self, event: tk.Event) -> str | None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return None
        column = self.tree.identify_column(event.x)
        if column != f"#{len(self.tree['columns'])}":
            return None
        item = self.tree.identify_row(event.y)
        if not item:
            return "break"
        self._toggle_checkbox(item)
        return "break"

    def _toggle_checkbox(self, item: str) -> None:
        row_id = self._item_to_row.get(item)
        if not row_id:
            return
        current = self._selection.get(row_id, False)
        new_state = not current
        self._selection[row_id] = new_state
        self.tree.set(item, "checkbox", "☑" if new_state else "☐")

    def _confirm_selected(self) -> None:
        changed = False
        for row_id, selected in list(self._selection.items()):
            if not selected:
                continue
            row = self._rows.get(row_id)
            if not row or not row.match_id:
                continue
            self.state_store.set_status(row.group_id, row.match_id, "confirmed")
            self._selection[row_id] = False
            changed = True
        if changed:
            self.state_store.save()
            self.refresh()

    def _remove_selected(self) -> None:
        changed = False
        for row_id, selected in list(self._selection.items()):
            if not selected:
                continue
            row = self._rows.get(row_id)
            if not row or not row.match_id:
                continue
            self.state_store.set_status(row.group_id, row.match_id, "removed")
            self._selection.pop(row_id, None)
            changed = True
        if changed:
            self.state_store.save()
            self.refresh()

    def _on_reload_clicked(self) -> None:
        if self.on_reload:
            self.on_reload()
        else:
            self.refresh()

    def _focus_unconfirmed(self) -> None:
        self._set_filter("pending")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _on_filter_selected(self, _event: tk.Event | None = None) -> None:
        label = self.filter_label_var.get()
        key = self._filter_label_to_key.get(label, "all")
        self._set_filter(key)

    def _on_sort_selected(self, _event: tk.Event | None = None) -> None:
        label = self.sort_label_var.get()
        key = self._sort_label_to_key.get(label, "name")
        self.sort_var.set(key)
        self.refresh()

    def _on_threshold_change(self, value: str) -> None:
        try:
            numeric = float(value)
        except ValueError:
            numeric = float(self.threshold_var.get())
        self.threshold_display_var.set(f"{numeric * 100:.0f}%")
        self._apply_filters()

    def _set_filter(self, key: str) -> None:
        self.filter_var.set(key)
        self.filter_label_var.set(self._label_for_filter(key))
        self._apply_filters()

    def _label_for_filter(self, key: str) -> str:
        return next((label for option, label in self.FILTER_OPTIONS if option == key), self.FILTER_OPTIONS[0][1])

    def _label_for_sort(self, key: str) -> str:
        return next((label for option, label in self.SORT_OPTIONS if option == key), self.SORT_OPTIONS[0][1])

class AutoUpdateDialog(tk.Toplevel):
    SOURCE_OPTIONS = (
        ("HTTP_FILE", "HTTP файл"),
        ("GOOGLE_SHEETS", "Google Sheets"),
        ("XML_FEED", "XML feed"),
    )

    def __init__(
        self,
        master: tk.Misc,
        *,
        supplier: str,
        price_name: str,
        templates: Sequence[ImportTemplate],
        current_template: str | None,
        initial_settings: Mapping[str, object],
    ) -> None:
        super().__init__(master)
        self.title("Налаштувати автооновлення")
        self.transient(master)
        self.grab_set()
        self.resizable(False, False)

        self.result: Optional[Dict[str, object]] = None

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        info = ttk.LabelFrame(container, text="Прайс")
        info.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(info, text="Постачальник:").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info, text=supplier, font=("TkDefaultFont", 10, "bold")).grid(
            row=0, column=1, sticky=tk.W, padx=(6, 0)
        )
        ttk.Label(info, text="Прайс:").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Label(info, text=price_name).grid(row=1, column=1, sticky=tk.W, padx=(6, 0), pady=(4, 0))

        settings = ttk.LabelFrame(container, text="Джерело")
        settings.pack(fill=tk.BOTH, expand=True)

        self.source_var = tk.StringVar(
            value=str(initial_settings.get("source_type") or "HTTP_FILE")
        )
        self.url_var = tk.StringVar(value=str(initial_settings.get("url") or ""))
        self.auth_var = tk.StringVar(value=str(initial_settings.get("auth_token") or ""))
        headers_text = initial_settings.get("headers") or ""
        if isinstance(headers_text, dict):
            headers_text = "\n".join(f"{k}: {v}" for k, v in headers_text.items())
        self.schedule_var = tk.StringVar(value=str(initial_settings.get("schedule") or ""))

        ttk.Label(settings, text="Тип джерела:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        source_combo = ttk.Combobox(
            settings,
            state="readonly",
            values=[label for _value, label in self.SOURCE_OPTIONS],
            width=26,
        )
        source_combo.grid(row=0, column=1, sticky=tk.W, pady=4)
        source_map = {label: value for value, label in self.SOURCE_OPTIONS}
        label_map = {value: label for value, label in self.SOURCE_OPTIONS}
        source_combo.set(label_map.get(self.source_var.get(), "HTTP файл"))

        def on_source_selected(_event: object) -> None:
            self.source_var.set(source_map.get(source_combo.get(), "HTTP_FILE"))

        source_combo.bind("<<ComboboxSelected>>", on_source_selected)

        ttk.Label(settings, text="URL / ID:").grid(row=1, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.url_var, width=46).grid(
            row=1, column=1, sticky=tk.EW, pady=4
        )

        ttk.Label(settings, text="Auth token / headers:").grid(
            row=2, column=0, sticky=tk.NW, padx=(0, 8), pady=4
        )
        self.headers_text = tk.Text(settings, width=46, height=4)
        self.headers_text.grid(row=2, column=1, sticky=tk.EW, pady=4)
        if headers_text:
            self.headers_text.insert("1.0", str(headers_text))

        ttk.Label(settings, text="Розклад:").grid(row=3, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.schedule_var, width=46).grid(
            row=3, column=1, sticky=tk.EW, pady=4
        )

        ttk.Label(settings, text="Auth token:").grid(row=4, column=0, sticky=tk.W, padx=(0, 8), pady=4)
        ttk.Entry(settings, textvariable=self.auth_var, width=46).grid(
            row=4, column=1, sticky=tk.EW, pady=4
        )

        template_frame = ttk.LabelFrame(container, text="Шаблон імпорту")
        template_frame.pack(fill=tk.X, pady=(8, 0))
        template_names = sorted({template.supplier for template in templates} or {supplier})
        self.template_var = tk.StringVar(
            value=str(
                current_template
                if current_template in template_names
                else (template_names[0] if template_names else "")
            )
        )
        ttk.Combobox(
            template_frame,
            state="readonly",
            values=template_names,
            textvariable=self.template_var,
            width=40,
        ).pack(side=tk.LEFT, padx=(8, 0), pady=8)

        button_bar = ttk.Frame(container)
        button_bar.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(button_bar, text="Скасувати", command=self._on_cancel).pack(
            side=tk.RIGHT
        )
        ttk.Button(button_bar, text="Зберегти", command=self._on_save).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        ttk.Button(button_bar, text="Запустити зараз", command=self._on_run).pack(
            side=tk.RIGHT, padx=(0, 8)
        )

        self.bind("<Escape>", lambda _event: self._on_cancel())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.geometry(_center_dialog(self, master))

    def _collect_settings(self) -> Optional[Dict[str, object]]:
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning(
                "Автооновлення", "Вкажіть URL або ID джерела.", parent=self
            )
            return None
        template = self.template_var.get().strip()
        if not template:
            messagebox.showwarning(
                "Автооновлення", "Оберіть шаблон імпорту.", parent=self
            )
            return None
        headers_raw = self.headers_text.get("1.0", tk.END).strip()
        settings: Dict[str, object] = {
            "source_type": self.source_var.get(),
            "url": url,
            "auth_token": self.auth_var.get().strip(),
            "headers": headers_raw,
            "schedule": self.schedule_var.get().strip(),
            "template": template,
        }
        return settings

    def _on_save(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return
        self.result = {"action": "save", "settings": settings}
        self.destroy()

    def _on_run(self) -> None:
        settings = self._collect_settings()
        if settings is None:
            return
        self.result = {"action": "run", "settings": settings}
        self.destroy()

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()


class ImportSettingsDialog(tk.Toplevel):
    """Dialog window for configuring import column mapping."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        path: str,
        supplier: str,
        headers: Sequence[str],
        preview_rows: Sequence[Dict[str, str]],
        required_fields: Sequence[str],
        optional_fields: Sequence[str],
        initial_mapping: Dict[str, str],
        templates: Sequence[ImportTemplate],
        current_template: Optional[ImportTemplate],
    ) -> None:
        super().__init__(master)
        self.title("Налаштування імпорту")
        self.resizable(True, True)
        self.transient(master)
        self.grab_set()

        self.headers = list(dict.fromkeys(headers))
        self.preview_rows = list(preview_rows)
        self.required_fields = list(required_fields)
        self.optional_fields = list(optional_fields)
        self.initial_mapping = dict(initial_mapping)
        self.templates = list(templates)
        self.current_template = current_template
        self.result: Optional[Dict[str, object]] = None

        self.column_vars: Dict[str, tk.StringVar] = {}
        for field in self.required_fields + self.optional_fields:
            self.column_vars[field] = tk.StringVar(value=self.initial_mapping.get(field, ""))

        self.save_template_var = tk.BooleanVar(value=True)
        self.template_var = tk.StringVar()

        container = ttk.Frame(self, padding=12)
        container.pack(fill=tk.BOTH, expand=True)

        info_frame = ttk.Frame(container)
        info_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(info_frame, text="Файл:").grid(row=0, column=0, sticky=tk.W)
        ttk.Label(info_frame, text=path, font=("TkDefaultFont", 9, "bold")).grid(
            row=0, column=1, sticky=tk.W, padx=(4, 0)
        )
        ttk.Label(info_frame, text="Постачальник:").grid(row=1, column=0, sticky=tk.W, pady=(4, 0))
        ttk.Label(info_frame, text=supplier).grid(row=1, column=1, sticky=tk.W, padx=(4, 0), pady=(4, 0))

        template_names = ["Автовизначення"]
        templates_by_name: Dict[str, ImportTemplate] = {}
        for template in self.templates:
            name = template.supplier
            if name in templates_by_name:
                continue
            templates_by_name[name] = template
            template_names.append(name)
        self.templates_by_name = templates_by_name

        if template_names[1:]:
            template_frame = ttk.Frame(container)
            template_frame.pack(fill=tk.X, pady=(0, 12))
            ttk.Label(template_frame, text="Шаблон:").pack(side=tk.LEFT)
            template_combo = ttk.Combobox(
                template_frame,
                state="readonly",
                values=template_names,
                textvariable=self.template_var,
                width=40,
            )
            template_combo.pack(side=tk.LEFT, padx=(6, 0))
            template_combo.bind("<<ComboboxSelected>>", self._on_template_selected)
            ttk.Button(
                template_frame,
                text="Скинути",
                command=self._use_initial_mapping,
            ).pack(side=tk.LEFT, padx=(6, 0))
        else:
            self.template_var.set("Автовизначення")

        mapping_frame = ttk.LabelFrame(container, text="Відповідність колонок")
        mapping_frame.pack(fill=tk.X, pady=(0, 12))
        mapping_frame.columnconfigure(1, weight=1)

        combobox_values = [""] + self.headers
        for row_index, field in enumerate(self.required_fields + self.optional_fields):
            is_required = field in self.required_fields
            label_text = field.upper() if field in {"sku", "name", "price"} else field
            if is_required:
                label_text = f"{label_text} *"
            ttk.Label(mapping_frame, text=label_text).grid(
                row=row_index, column=0, sticky=tk.W, padx=(6, 4), pady=4
            )

            combo = ttk.Combobox(
                mapping_frame,
                values=combobox_values,
                textvariable=self.column_vars[field],
                state="readonly",
            )
            combo.grid(row=row_index, column=1, sticky=tk.EW, padx=(0, 6), pady=4)

        save_template_check = ttk.Checkbutton(
            mapping_frame,
            text="Зберегти як шаблон для постачальника",
            variable=self.save_template_var,
        )
        save_template_check.grid(
            row=len(self.required_fields + self.optional_fields),
            column=0,
            columnspan=2,
            sticky=tk.W,
            padx=6,
            pady=(4, 0),
        )

        preview_frame = ttk.LabelFrame(container, text="Попередній перегляд")
        preview_frame.pack(fill=tk.BOTH, expand=True)

        if self.headers:
            tree = ttk.Treeview(
                preview_frame,
                columns=self.headers,
                show="headings",
                height=8,
            )
            tree.grid(row=0, column=0, sticky="nsew")
            preview_frame.columnconfigure(0, weight=1)
            preview_frame.rowconfigure(0, weight=1)

            y_scroll = ttk.Scrollbar(preview_frame, orient=tk.VERTICAL, command=tree.yview)
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll = ttk.Scrollbar(preview_frame, orient=tk.HORIZONTAL, command=tree.xview)
            x_scroll.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

            for header in self.headers:
                tree.heading(header, text=header)
                tree.column(header, width=160, anchor=tk.W)

            for row in self.preview_rows:
                tree.insert(
                    "",
                    tk.END,
                    values=[row.get(header, "") for header in self.headers],
                )
        else:
            ttk.Label(
                preview_frame,
                text="Не вдалося визначити заголовки. Файл може бути порожнім.",
            ).pack(padx=12, pady=12, anchor=tk.W)

        buttons = ttk.Frame(container)
        buttons.pack(fill=tk.X, pady=(12, 0))
        buttons.columnconfigure(0, weight=1)

        ttk.Button(buttons, text="Скасувати", command=self._on_cancel).grid(
            row=0, column=1, padx=(0, 8)
        )
        ttk.Button(buttons, text="Імпортувати", command=self._on_ok).grid(row=0, column=2)

        self.bind("<Return>", lambda _event: self._on_ok())
        self.bind("<Escape>", lambda _event: self._on_cancel())

        if current_template and current_template.supplier in self.templates_by_name:
            self.template_var.set(current_template.supplier)
            self._apply_mapping(current_template.column_mapping)
        else:
            self.template_var.set("Автовизначення")

    def _use_initial_mapping(self) -> None:
        self.template_var.set("Автовизначення")
        self._apply_mapping(self.initial_mapping)

    def _apply_mapping(self, mapping: Dict[str, str]) -> None:
        for field, var in self.column_vars.items():
            column = mapping.get(field, "")
            if column not in self.headers:
                column = ""
            var.set(column)

    def _on_template_selected(self, _event: object) -> None:
        name = self.template_var.get()
        if name == "Автовизначення":
            self._apply_mapping(self.initial_mapping)
            return
        template = self.templates_by_name.get(name)
        if template:
            self._apply_mapping(template.column_mapping)

    def _on_cancel(self) -> None:
        self.result = None
        self.destroy()

    def _on_ok(self) -> None:
        mapping: Dict[str, str] = {}
        used_columns: set[str] = set()

        for field, var in self.column_vars.items():
            value = var.get().strip()
            if not value:
                if field in self.required_fields:
                    messagebox.showerror(
                        "Налаштування імпорту",
                        "Необхідно обрати колонку для поля '{}'.".format(field),
                        parent=self,
                    )
                    return
                continue

            if value not in self.headers:
                messagebox.showerror(
                    "Налаштування імпорту",
                    "Колонку '{}' не знайдено у файлі.".format(value),
                    parent=self,
                )
                return

            if field in self.required_fields and value in used_columns:
                messagebox.showerror(
                    "Налаштування імпорту",
                    "Колонка '{}' вже використовується для іншого обов'язкового поля.".format(
                        value
                    ),
                    parent=self,
                )
                return

            mapping[field] = value
            if field in self.required_fields:
                used_columns.add(value)

        missing = [field for field in self.required_fields if field not in mapping]
        if missing:
            messagebox.showerror(
                "Налаштування імпорту",
                "Не вказано всі обов'язкові поля: {}.".format(", ".join(missing)),
                parent=self,
            )
            return

        self.result = {
            "column_mapping": mapping,
            "save_template": bool(self.save_template_var.get()),
        }
        self.destroy()


def run_app(*, data_dir: str | Path = ".price_compare_data", tags_config: str | Path | None = None) -> None:
    """Run the Tkinter application."""

    app = PriceCompareApp(data_dir=data_dir, tags_config=tags_config)
    app.mainloop()

