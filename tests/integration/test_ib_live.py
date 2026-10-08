import asyncio
import logging
import os
from collections.abc import AsyncIterator, Iterator

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
from asof_store.ib_models import (
    ExchangeResponse,
    Instrument,
    NewProduct,
    ProductsResponse,
    product_name2id,
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

logger = logging.getLogger(__name__)


def _assert_exchange_response(response: ExchangeResponse) -> None:
    exchanges = response.exchanges
    product_count = response.product_count
    product_type_count = response.product_type_count

    # consistent data
    assert product_count == len(exchanges)
    assert product_count == len({(e.id, e.country) for e in exchanges})
    assert {e.id for e in exchanges} == {
        p for _, pc in product_type_count.items() for p in pc
    }

    # must not empty
    for p in product_type_count:
        if p not in product_name2id:
            logger.warning(
                f"Product type {p} is in product_type_count but not in product_name2id"
            )
    assert exchanges


def _assert_products(
    response: ProductsResponse,
    *,
    total_count: int,
) -> None:
    products = response.products
    assert len(products) == min(_PAGE_SIZE, total_count)
    assert all(isinstance(product, Instrument) for product in products)


def test_live_sync_ib_endpoints() -> None:
    exchanges = get_exchanges(timeout=_TIMEOUT)
    _assert_exchange_response(exchanges)

    summary = get_instrument_summary(timeout=_TIMEOUT)
    assert len(summary) > 0
    for si in summary:
        products = get_products_by_filters(
            si.product_type,
            page_size=_PAGE_SIZE,
            timeout=_TIMEOUT,
        )
        _assert_products(products, total_count=si.total_count)
        break


def test_live_async_ib_endpoints() -> None:
    async def exercise() -> None:
        exchanges = await get_exchanges_async(timeout=_TIMEOUT)
        _assert_exchange_response(exchanges)

        summary = await get_instrument_summary_async(timeout=_TIMEOUT)
        for si in summary:
            products = await get_products_by_filters_async(
                si.product_type,
                page_size=_PAGE_SIZE,
                timeout=_TIMEOUT,
            )
            _assert_products(products, total_count=si.total_count)
            break

    asyncio.run(exercise())


@pytest.mark.parametrize("new_product", ["T", "F"])
def test_live_new_product_filter(new_product: NewProduct) -> None:
    summary = get_instrument_summary(
        product_type=["STK"],
        product_country=["US"],
        new_product=new_product,
        timeout=_TIMEOUT,
    )
    assert len(summary) == 1
    total_count = summary[0].total_count
    assert total_count > 0

    products = get_products_by_filters(
        "STK",
        page_size=_PAGE_SIZE,
        product_country=["US"],
        new_product=new_product,
        timeout=_TIMEOUT,
    )
    _assert_products(products, total_count=total_count)
    assert all(product.country == "US" for product in products.products)


@pytest.mark.parametrize("new_product", ["T", "F"])
def test_live_async_new_product_filter(new_product: NewProduct) -> None:
    async def exercise() -> None:
        summary = await get_instrument_summary_async(
            product_type=["STK"],
            product_country=["US"],
            new_product=new_product,
            timeout=_TIMEOUT,
        )
        assert len(summary) == 1
        total_count = summary[0].total_count
        assert total_count > 0

        products = await get_products_by_filters_async(
            "STK",
            page_size=_PAGE_SIZE,
            product_country=["US"],
            new_product=new_product,
            timeout=_TIMEOUT,
        )
        _assert_products(products, total_count=total_count)
        assert all(product.country == "US" for product in products.products)

    asyncio.run(exercise())


def test_live_sync_scraper_yields_instruments() -> None:
    instruments: Iterator[Instrument] = scrape_instruments(
        page_size=_PAGE_SIZE,
        product_type=["STK"],
        product_country=["US"],
        timeout=_TIMEOUT,
    )
    try:
        instrument = next(instruments)
    finally:
        instruments.close()

    assert isinstance(instrument, Instrument)
    assert instrument.model_dump(exclude_unset=True)
    assert instrument.product_type == "STK"
    assert instrument.country == "US"


def test_live_async_scraper_yields_instruments() -> None:
    async def exercise() -> None:
        instruments: AsyncIterator[Instrument] = scrape_instruments_async(
            page_size=_PAGE_SIZE,
            product_type=["STK"],
            product_country=["US"],
            timeout=_TIMEOUT,
        )
        try:
            instrument = await anext(instruments)
        finally:
            await instruments.aclose()

        assert isinstance(instrument, Instrument)
        assert instrument.model_dump(exclude_unset=True)
        assert instrument.product_type == "STK"
        assert instrument.country == "US"

    asyncio.run(exercise())
