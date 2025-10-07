"""Entry point script for building standalone executables.

This thin wrapper exists so that tools like PyInstaller can bundle the
`price_compare` CLI into a single executable without additional
configuration. It simply proxies execution to the package's CLI
`main()` function.
"""

from price_compare.cli import main


if __name__ == "__main__":
    main()
