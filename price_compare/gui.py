"""Graphical user interface for the price comparison toolkit."""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Dict, List, Sequence

from .comparator import PriceComparator
from .io import PriceListExporter, PriceListImporter
from .models import PriceList, Product
from .repository import PriceListRepository
from .search import ProductSearch
from .tagging import Tagger


def _format_price(product: Product) -> str:
    return f"{product.price:,.2f} {product.currency}".replace(",", " ")


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
        self.tagger = Tagger.from_json(tags_config) if tags_config else Tagger()
        self._tags_config_path: str | None = str(tags_config) if tags_config else None

        self.price_lists: Dict[str, PriceList] = {}

        self._create_menu()
        self._create_widgets()
        self.refresh_data()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _create_widgets(self) -> None:
        container = ttk.Notebook(self)
        container.pack(fill=tk.BOTH, expand=True)

        self.catalog_tab = ttk.Frame(container)
        self.search_tab = ttk.Frame(container)
        self.compare_tab = ttk.Frame(container)
        self.best_tab = ttk.Frame(container)

        container.add(self.catalog_tab, text="Каталог постачальників")
        container.add(self.search_tab, text="Пошук")
        container.add(self.compare_tab, text="Порівняння")
        container.add(self.best_tab, text="Найкращі пропозиції")

        self._build_catalog_tab()
        self._build_search_tab()
        self._build_compare_tab()
        self._build_best_tab()

    def _create_menu(self) -> None:
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        settings_menu = tk.Menu(menubar, tearoff=False)
        menubar.add_cascade(label="Налаштування", menu=settings_menu)
        settings_menu.add_command(
            label="Завантажити правила тегування",
            command=self._load_tag_rules,
        )
        settings_menu.add_separator()
        settings_menu.add_command(label="Вийти", command=self.destroy)

    def _build_catalog_tab(self) -> None:
        toolbar = ttk.Frame(self.catalog_tab)
        toolbar.pack(fill=tk.X, padx=8, pady=4)

        ttk.Button(toolbar, text="Імпорт прайсу", command=self._import_price_list).pack(
            side=tk.LEFT
        )
        ttk.Button(toolbar, text="Експорт постачальника", command=self._export_supplier).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(toolbar, text="Видалити", command=self._delete_supplier).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ttk.Button(toolbar, text="Оновити", command=self.refresh_data).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        body = ttk.Panedwindow(self.catalog_tab, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        left_frame = ttk.Frame(body)
        right_frame = ttk.Frame(body)
        body.add(left_frame, weight=1)
        body.add(right_frame, weight=3)

        ttk.Label(left_frame, text="Постачальники").pack(anchor=tk.W)
        self.suppliers_list = tk.Listbox(left_frame, exportselection=False)
        self.suppliers_list.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.suppliers_list.bind("<<ListboxSelect>>", lambda _event: self._show_supplier_products())

        ttk.Label(right_frame, text="Товари постачальника").pack(anchor=tk.W)
        columns = ("sku", "name", "price", "tags")
        self.products_tree = ttk.Treeview(
            right_frame,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headings = {
            "sku": "SKU",
            "name": "Назва",
            "price": "Ціна",
            "tags": "Теги",
        }
        widths = {
            "sku": 140,
            "name": 360,
            "price": 120,
            "tags": 220,
        }
        for column in columns:
            self.products_tree.heading(column, text=headings[column])
            self.products_tree.column(column, width=widths[column], anchor=tk.W)
        self.products_tree.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

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

    def _build_compare_tab(self) -> None:
        container = ttk.Frame(self.compare_tab)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        sku_frame = ttk.LabelFrame(container, text="Пошук за SKU")
        sku_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(sku_frame, text="SKU:").pack(side=tk.LEFT, padx=4, pady=4)
        self.compare_sku_entry = ttk.Entry(sku_frame, width=24)
        self.compare_sku_entry.pack(side=tk.LEFT, padx=4, pady=4)
        ttk.Button(sku_frame, text="Знайти", command=self._compare_by_sku).pack(
            side=tk.LEFT, padx=4, pady=4
        )

        name_frame = ttk.LabelFrame(container, text="Пошук за назвою")
        name_frame.pack(fill=tk.X)

        ttk.Label(name_frame, text="Назва:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self.compare_name_entry = ttk.Entry(name_frame)
        self.compare_name_entry.grid(row=0, column=1, sticky=tk.EW, padx=4, pady=4)

        ttk.Label(name_frame, text="Поріг схожості:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)
        self.compare_threshold_var = tk.DoubleVar(value=0.75)
        ttk.Scale(name_frame, from_=0.4, to=1.0, orient=tk.HORIZONTAL, variable=self.compare_threshold_var).grid(
            row=0, column=3, sticky=tk.EW, padx=4, pady=4
        )
        ttk.Button(name_frame, text="Порівняти", command=self._compare_by_name).grid(
            row=0, column=4, sticky=tk.W, padx=4, pady=4
        )
        name_frame.columnconfigure(1, weight=1)
        name_frame.columnconfigure(3, weight=1)

        ttk.Button(container, text="Експорт пропозицій", command=self._export_comparison).pack(
            anchor=tk.W, pady=8
        )

        columns = ("sku", "name", "supplier", "price")
        self.compare_tree = ttk.Treeview(
            container,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headers = {
            "sku": "SKU",
            "name": "Назва",
            "supplier": "Постачальник",
            "price": "Ціна",
        }
        widths = {"sku": 140, "name": 320, "supplier": 160, "price": 120}
        for column in columns:
            self.compare_tree.heading(column, text=headers[column])
            self.compare_tree.column(column, width=widths[column], anchor=tk.W)
        self.compare_tree.pack(fill=tk.BOTH, expand=True)

        self.comparison_results: List[Product] = []

    def _build_best_tab(self) -> None:
        actions = ttk.Frame(self.best_tab)
        actions.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(actions, text="Оновити", command=self._show_best_offers).pack(side=tk.LEFT)
        ttk.Button(actions, text="Експорт", command=self._export_best_offers).pack(
            side=tk.LEFT, padx=8
        )

        columns = ("sku", "name", "supplier", "price")
        self.best_tree = ttk.Treeview(
            self.best_tab,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        headers = {
            "sku": "SKU",
            "name": "Назва",
            "supplier": "Постачальник",
            "price": "Ціна",
        }
        widths = {"sku": 140, "name": 360, "supplier": 160, "price": 120}
        for column in columns:
            self.best_tree.heading(column, text=headers[column])
            self.best_tree.column(column, width=widths[column], anchor=tk.W)
        self.best_tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.best_offers: List[Product] = []

    # ------------------------------------------------------------------
    # Data helpers
    # ------------------------------------------------------------------
    def refresh_data(self) -> None:
        try:
            self.price_lists = self.repository.load_all()
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося завантажити дані: {exc}")
            self.price_lists = {}
        self._populate_suppliers()
        self._populate_supplier_dropdown()
        self._show_supplier_products()
        self._show_best_offers()

    def _populate_suppliers(self) -> None:
        self.suppliers_list.delete(0, tk.END)
        for supplier in sorted(self.price_lists):
            price_list = self.price_lists[supplier]
            count = len(price_list.products)
            self.suppliers_list.insert(tk.END, f"{supplier} ({count})")

    def _populate_supplier_dropdown(self) -> None:
        suppliers = sorted(self.price_lists)
        self.search_supplier["values"] = ["Усі"] + suppliers
        self.search_supplier.set("Усі")

    def _selected_supplier(self) -> str | None:
        selection = self.suppliers_list.curselection()
        if not selection:
            return None
        index = selection[0]
        item = self.suppliers_list.get(index)
        return item.split(" (")[0]

    def _show_supplier_products(self) -> None:
        for item in self.products_tree.get_children():
            self.products_tree.delete(item)

        supplier = self._selected_supplier()
        if not supplier:
            return

        price_list = self.price_lists.get(supplier)
        if not price_list:
            return

        for product in price_list.products:
            tags = ", ".join(sorted(product.tags))
            self.products_tree.insert(
                "",
                tk.END,
                values=(product.sku, product.name, _format_price(product), tags),
            )

    # ------------------------------------------------------------------
    # Import / export actions
    # ------------------------------------------------------------------
    def _import_price_list(self) -> None:
        path = filedialog.askopenfilename(
            title="Оберіть файл прайсу",
            filetypes=(
                ("CSV файли", "*.csv"),
                ("JSON файли", "*.json"),
                ("Усі підтримувані", "*.csv *.json"),
            ),
        )
        if not path:
            return

        supplier = simpledialog.askstring("Постачальник", "Назва постачальника:")
        if not supplier:
            messagebox.showinfo("Імпорт перервано", "Назва постачальника не вказана.")
            return

        try:
            price_list = self.importer.load(path, supplier=supplier)
            self.tagger.apply(price_list.products)
            self.repository.save(price_list)
        except Exception as exc:
            messagebox.showerror("Помилка імпорту", f"Не вдалося імпортувати прайс: {exc}")
            return

        messagebox.showinfo("Готово", f"Імпортовано {len(price_list.products)} позицій.")
        self.refresh_data()

    def _export_supplier(self) -> None:
        supplier = self._selected_supplier()
        if not supplier:
            messagebox.showwarning("Експорт", "Оберіть постачальника зі списку.")
            return

        price_list = self.price_lists.get(supplier)
        if not price_list:
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти як",
            defaultextension=".csv",
            filetypes=(("CSV файл", "*.csv"), ("JSON файл", "*.json")),
        )
        if not path:
            return

        try:
            self.exporter.export(price_list.products, path)
        except Exception as exc:
            messagebox.showerror("Помилка експорту", f"Не вдалося зберегти файл: {exc}")
            return

        messagebox.showinfo("Готово", "Дані успішно збережено.")

    def _delete_supplier(self) -> None:
        supplier = self._selected_supplier()
        if not supplier:
            messagebox.showwarning("Видалення", "Оберіть постачальника зі списку.")
            return

        if not messagebox.askyesno(
            "Видалення", f"Видалити прайс постачальника '{supplier}'?"
        ):
            return

        try:
            self.repository.delete(supplier)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося видалити дані: {exc}")
            return

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

        search = ProductSearch(self.price_lists.values())
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

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------
    def _compare_by_sku(self) -> None:
        sku = self.compare_sku_entry.get().strip()
        if not sku:
            messagebox.showwarning("Порівняння", "Вкажіть SKU.")
            return

        comparator = PriceComparator(self.price_lists.values())
        entry = comparator.compare_by_sku(sku)
        offers = entry.offers if entry else []
        self._update_comparison_tree(offers)

    def _compare_by_name(self) -> None:
        name = self.compare_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Порівняння", "Вкажіть назву товару.")
            return

        comparator = PriceComparator(self.price_lists.values())
        entries = comparator.compare_by_name(name, threshold=float(self.compare_threshold_var.get()))
        offers: List[Product] = []
        for entry in entries:
            offers.extend(entry.offers)
        self._update_comparison_tree(offers)

    def _update_comparison_tree(self, offers: Sequence[Product]) -> None:
        for item in self.compare_tree.get_children():
            self.compare_tree.delete(item)

        self.comparison_results = sorted(offers, key=lambda product: product.price)
        for product in self.comparison_results:
            self.compare_tree.insert(
                "",
                tk.END,
                values=(product.sku, product.name, product.supplier or "", _format_price(product)),
            )

    def _export_comparison(self) -> None:
        if not self.comparison_results:
            messagebox.showwarning("Експорт", "Немає даних для експорту.")
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти пропозиції",
            defaultextension=".csv",
            filetypes=(("CSV файл", "*.csv"), ("JSON файл", "*.json")),
        )
        if not path:
            return

        try:
            self.exporter.export(self.comparison_results, path)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося зберегти: {exc}")
            return

        messagebox.showinfo("Готово", "Дані експортовано.")

    # ------------------------------------------------------------------
    # Best offers
    # ------------------------------------------------------------------
    def _show_best_offers(self) -> None:
        for item in self.best_tree.get_children():
            self.best_tree.delete(item)

        comparator = PriceComparator(self.price_lists.values())
        entries = comparator.best_offers()
        offers: List[Product] = []
        for entry in entries:
            best = entry.best_offer
            if best:
                offers.append(best)

        self.best_offers = sorted(offers, key=lambda product: product.price)

        for product in self.best_offers:
            self.best_tree.insert(
                "",
                tk.END,
                values=(product.sku, product.name, product.supplier or "", _format_price(product)),
            )

    def _export_best_offers(self) -> None:
        if not self.best_offers:
            messagebox.showwarning("Експорт", "Немає пропозицій для експорту.")
            return

        path = filedialog.asksaveasfilename(
            title="Зберегти найкращі пропозиції",
            defaultextension=".csv",
            filetypes=(("CSV файл", "*.csv"), ("JSON файл", "*.json")),
        )
        if not path:
            return

        try:
            self.exporter.export(self.best_offers, path)
        except Exception as exc:
            messagebox.showerror("Помилка", f"Не вдалося зберегти: {exc}")
            return

        messagebox.showinfo("Готово", "Найкращі пропозиції експортовано.")


def run_app(*, data_dir: str | Path = ".price_compare_data", tags_config: str | Path | None = None) -> None:
    """Run the Tkinter application."""

    app = PriceCompareApp(data_dir=data_dir, tags_config=tags_config)
    app.mainloop()

