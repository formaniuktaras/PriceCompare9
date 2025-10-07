# PriceCompare1

Комплексний інструмент для імпорту, каталогізації та порівняння прайс-листів різних постачальників. Додаток дозволяє об'єднати товари з різних джерел, автоматично визначати теги, швидко знаходити потрібні позиції та вивантажувати результати у зручному форматі.

## Можливості

- Імпорт прайсів у форматах CSV та JSON з автоматичним визначенням постачальника.
- Збереження прайсів у локальному сховищі для подальших операцій.
- Автоматичне присвоєння тегів товарам на основі ключових слів або власних правил.
- Пошук по товарах із підтримкою не лише точних збігів, але й «розумного» (fuzzy) пошуку.
- Детальне порівняння пропозицій по SKU чи за назвою товару.
- Перелік найвигідніших пропозицій серед усіх постачальників.
- Експорт результатів пошуку у CSV або JSON для подальшої роботи.
- Просте очищення сховища.

## Встановлення

Вимоги: Python 3.11+

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt  # якщо потрібно, але проєкт працює на стандартній бібліотеці
```

## Приклади використання

### Імпорт прайсу

```bash
python -m price_compare.cli import "Supplier A" data/supplier_a.csv
```

### Перелік постачальників

```bash
python -m price_compare.cli suppliers
```

### Перегляд товарів постачальника

```bash
python -m price_compare.cli products "Supplier A" --limit 20
```

### Пошук товарів

```bash
python -m price_compare.cli search "laptop" --fuzzy --threshold 0.7
```

### Порівняння пропозицій по SKU

```bash
python -m price_compare.cli compare ABC-123
```

### Порівняння за назвою

```bash
python -m price_compare.cli compare-name "Lenovo ThinkPad" --threshold 0.6
```

### Найкращі пропозиції

```bash
python -m price_compare.cli best --limit 15
```

### Експорт результатів

```bash
python -m price_compare.cli export "router" reports/router_offers.csv --fuzzy
```

### Приклади тестових даних

У репозиторії містяться приклади CSV-файлів `sample_supplier_a.csv` та `sample_supplier_b.csv`, які можна використати для швидкого знайомства з інтерфейсом.

### Очищення сховища

```bash
python -m price_compare.cli clear
```

## Кастомні правила тегування

Створіть JSON-файл з описом правил:

```json
{
  "rules": [
    {"tag": "office", "keywords": ["chair", "desk", "table"]},
    {"tag": "gaming", "keywords": ["gaming", "rgb", "geforce"]}
  ]
}
```

та передайте шлях до файлу при імпорті:

```bash
python -m price_compare.cli --tags-config rules.json import "Supplier B" data/supplier_b.csv
```

## Структура проєкту

- `price_compare/models.py` — основні моделі.
- `price_compare/io.py` — імпорт та експорт даних.
- `price_compare/tagging.py` — логіка авто-тегування.
- `price_compare/comparator.py` — інструменти порівняння.
- `price_compare/search.py` — пошук по товарах.
- `price_compare/repository.py` — сховище прайсів.
- `price_compare/cli.py` — командний інтерфейс.

## Створення виконуваного файлу (.exe)

1. Встановіть [PyInstaller](https://pyinstaller.org/):

   ```bash
   pip install pyinstaller
   ```

2. Запустіть збірку у режимі одного файлу без консольного вікна:

   ```bash
   pyinstaller --onefile --noconsole main.py
   ```

3. Готовий виконуваний файл буде знаходитись у каталозі `dist/` і матиме назву `main.exe`. За потреби перейменуйте його та розмістіть поруч із каталогом даних (наприклад, `.price_compare`), щоб зберегти доступ до імпортованих прайсів.

4. Запускайте застосунок подвійним кліком або через командний рядок:

   ```bash
   ./main.exe --help
   ```

> **Примітка.** Скрипт `main.py` є обгорткою над CLI пакету `price_compare`, тому всі команди й опції, описані вище, доступні і в зібраному `.exe`.

## Ліцензія

MIT (за потреби можна адаптувати).
