"""Graphical entry point used by PyInstaller builds."""

from price_compare.gui import run_app


def main() -> None:
    run_app()


if __name__ == "__main__":
    main()
