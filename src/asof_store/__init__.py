from ._abc import AsOfStoreABC
from ._store import AsOfStore
from ._view import AsOfView


def main() -> None:
    from .ib_scraper import main as ib_scraper_main

    raise SystemExit(ib_scraper_main())


__all__ = ["AsOfStore", "AsOfStoreABC", "AsOfView", "main"]
