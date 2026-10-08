import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

pytest.importorskip("zapros")

from asof_store.ib import (
    get_exchanges,
    get_exchanges_async,
    get_instrument_summary,
    get_instrument_summary_async,
    get_products_by_filters,
    get_products_by_filters_async,
    scrape_instruments,
    scrape_instruments_async,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("ASOF_STORE_RUN_IB_INTEGRATION") != "1",
        reason="set ASOF_STORE_RUN_IB_INTEGRATION=1 to enable live IB requests",
    ),
]

_TIMEOUT = 60
_PAGE_SIZE = 100


def _assert_exchange_response(response: dict[str, Any]) -> None:
    exchanges = response["exchanges"]
    assert exchanges
    assert all(
        isinstance(exchange, dict)
        and isinstance(exchange.get("id"), str)
        and isinstance(exchange.get("name"), str)
        for exchange in exchanges
    )


def _first_product_type(
    summary: list[dict[str, Any]],
) -> tuple[str, int]:
    for item in summary:
        product_type = item.get("productType")
        total_count = item.get("totalCount")
        assert isinstance(product_type, str) and product_type
        assert isinstance(total_count, int) and not isinstance(total_count, bool)
        assert total_count >= 0
        if total_count:
            return product_type, total_count
    pytest.fail("IB returned no products in its instrument summary")


def _assert_products(
    response: dict[str, Any],
    *,
    total_count: int,
) -> None:
    products = response["products"]
    assert len(products) == min(_PAGE_SIZE, total_count)
    assert all(isinstance(product, dict) for product in products)


def test_live_sync_ib_endpoints() -> None:
    _assert_exchange_response(get_exchanges(timeout=_TIMEOUT))

    summary = get_instrument_summary(timeout=_TIMEOUT)
    product_type, total_count = _first_product_type(summary)
    products = get_products_by_filters(
        product_type,
        page_size=_PAGE_SIZE,
        timeout=_TIMEOUT,
    )
    _assert_products(products, total_count=total_count)


def test_live_async_ib_endpoints() -> None:
    async def exercise() -> None:
        _assert_exchange_response(await get_exchanges_async(timeout=_TIMEOUT))

        summary = await get_instrument_summary_async(timeout=_TIMEOUT)
        product_type, total_count = _first_product_type(summary)
        products = await get_products_by_filters_async(
            product_type,
            page_size=_PAGE_SIZE,
            timeout=_TIMEOUT,
        )
        _assert_products(products, total_count=total_count)

    asyncio.run(exercise())


def test_live_sync_scraper_yields_instruments() -> None:
    instruments: Iterator[dict[str, Any]] = scrape_instruments(
        page_size=_PAGE_SIZE,
        timeout=_TIMEOUT,
    )
    try:
        instrument = next(instruments)
    finally:
        instruments.close()

    assert isinstance(instrument, dict)
    assert instrument


def test_live_async_scraper_yields_instruments() -> None:
    async def exercise() -> None:
        instruments: AsyncIterator[dict[str, Any]] = scrape_instruments_async(
            page_size=_PAGE_SIZE,
            timeout=_TIMEOUT,
        )
        try:
            instrument = await anext(instruments)
        finally:
            await instruments.aclose()

        assert isinstance(instrument, dict)
        assert instrument

    asyncio.run(exercise())
