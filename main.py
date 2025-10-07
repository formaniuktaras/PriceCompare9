"""Entry point script for building standalone executables.

This wrapper allows tools like PyInstaller to bundle the
``price_compare`` CLI into a single executable without additional
configuration. When launched without command-line arguments (for
instance by double-clicking ``main.exe`` that was produced with
``--noconsole``) the script shows a small usage pop-up explaining how to
use the tool.
"""

from __future__ import annotations

import sys
import textwrap

from price_compare.cli import main as cli_main


def _inform_no_arguments() -> None:
    """Display a short usage hint when the app is started without args."""

    message = textwrap.dedent(
        """
        Інструмент керується через командний рядок.

        Запустіть головний файл з аргументами, наприклад:
            main.exe suppliers
            main.exe import "Supplier" path\\to\\file.csv

        Повний перелік команд доступний через:
            main.exe --help
        """
    ).strip()

    try:
        import tkinter
        from tkinter import messagebox

        root = tkinter.Tk()
        root.withdraw()
        messagebox.showinfo("Price Compare", message)
        root.destroy()
        return
    except Exception:
        pass

    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Price Compare", 0)
        return
    except Exception:
        pass

    print(message)


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        _inform_no_arguments()
        return

    cli_main(argv)


if __name__ == "__main__":
    main()
