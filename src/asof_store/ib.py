from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

try:
    from pydantic import TypeAdapter
    from zapros import AsyncClient, Client, RequestContext
except ImportError as exc:
    raise ImportError(
        "The IB integration requires Pydantic and Zapros; install the 'scraper' extra"
    ) from exc

from .ib_models import (
    ExchangeResponse,
    Instrument,
    InstrumentSummaryItem,
    InstrumentSummaryRequest,
    ProductsByFiltersRequest,
    ProductsResponse,
)

_SUMMARY_ADAPTER = TypeAdapter(list[InstrumentSummaryItem])

_BASE_URL = "https://www.interactivebrokers.co.uk/webrest"
_EXCHANGES_URL = f"{_BASE_URL}/exchanges/"
_SUMMARY_URL = f"{_BASE_URL}/search/product-types/summary"
_PRODUCTS_URL = f"{_BASE_URL}/search/products-by-filters"


@contextmanager
def _using_client(client: Client | None) -> Generator[Client]:
    if client is not None:
        yield client
        return

    with Client() as owned_client:
        yield owned_client


@asynccontextmanager
async def _using_async_client(
    client: AsyncClient | None,
) -> AsyncGenerator[AsyncClient]:
    if client is not None:
        yield client
        return

    async with AsyncClient() as owned_client:
        yield owned_client


def _request_json(
    client: Client,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    timeout: float,
) -> Any:
    context: RequestContext = {"timeouts": {"total": timeout}}
    if method == "GET":
        response = client.get(url, context=context)
    else:
        response = client.post(url, json=payload, context=context)

    if not 200 <= response.status < 300:
        raise RuntimeError(
            f"IB request to {url} failed with HTTP status {response.status}"
        )
    return response.json


async def _request_json_async(
    client: AsyncClient,
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None,
    timeout: float,
) -> Any:
    context: RequestContext = {"timeouts": {"total": timeout}}
    if method == "GET":
        response = await client.get(url, context=context)
    else:
        response = await client.post(url, json=payload, context=context)

    if not 200 <= response.status < 300:
        raise RuntimeError(
            f"IB request to {url} failed with HTTP status {response.status}"
        )
    return response.json


def _validate_timeout(timeout: float) -> None:
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")


def _validate_page_size(page_size: int) -> None:
    if not 100 <= page_size <= 500 or page_size % 100 != 0:
        raise ValueError("page_size must be 100, 200, 300, 400, or 500")


def _product_totals(
    summary: list[InstrumentSummaryItem],
) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in summary:
        product_type = item.product_type
        total_count = item.total_count
        if product_type in totals:
            raise ValueError(f"Duplicate IB instrument summary: {product_type}")
        totals[product_type] = total_count
    return totals


def _validated_products(
    products: list[Instrument],
    *,
    product_type: str,
    page_number: int,
    expected_count: int,
) -> list[Instrument]:
    if len(products) != expected_count:
        raise ValueError(
            f"Unexpected product count for {product_type} page "
            f"{page_number}: expected {expected_count}, received {len(products)}"
        )
    return products


def get_exchanges(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> ExchangeResponse:
    """Return IB's exchange catalogue."""
    _validate_timeout(timeout)
    with _using_client(client) as active_client:
        result = _request_json(
            active_client,
            "GET",
            _EXCHANGES_URL,
            payload=None,
            timeout=timeout,
        )
    return ExchangeResponse.model_validate(result)


async def get_exchanges_async(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> ExchangeResponse:
    """Asynchronously return IB's exchange catalogue."""
    _validate_timeout(timeout)
    async with _using_async_client(client) as active_client:
        result = await _request_json_async(
            active_client,
            "GET",
            _EXCHANGES_URL,
            payload=None,
            timeout=timeout,
        )
    return ExchangeResponse.model_validate(result)


def get_instrument_summary(
    client: Any | None = None,
    *,
    domain: str = "uk",
    timeout: float = 30,
) -> list[InstrumentSummaryItem]:
    """Return the instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = InstrumentSummaryRequest(domain=domain).model_dump(by_alias=True)
    with _using_client(client) as active_client:
        result = _request_json(
            active_client,
            "POST",
            _SUMMARY_URL,
            payload=payload,
            timeout=timeout,
        )
    return _SUMMARY_ADAPTER.validate_python(result)


async def get_instrument_summary_async(
    client: Any | None = None,
    *,
    domain: str = "uk",
    timeout: float = 30,
) -> list[InstrumentSummaryItem]:
    """Asynchronously return instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = InstrumentSummaryRequest(domain=domain).model_dump(by_alias=True)
    async with _using_async_client(client) as active_client:
        result = await _request_json_async(
            active_client,
            "POST",
            _SUMMARY_URL,
            payload=payload,
            timeout=timeout,
        )
    return _SUMMARY_ADAPTER.validate_python(result)


def get_products_by_filters(
    product_type: str,
    client: Any | None = None,
    *,
    page_number: int = 1,
    page_size: int = 500,
    domain: str = "uk",
    timeout: float = 30,
) -> ProductsResponse:
    """Return one page of IB products for a product type."""
    if not product_type:
        raise ValueError("product_type must not be empty")
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    payload = ProductsByFiltersRequest(
        domain=domain,
        page_number=page_number,
        page_size=page_size,
        product_type=[product_type],
    ).model_dump(by_alias=True)
    with _using_client(client) as active_client:
        result = _request_json(
            active_client,
            "POST",
            _PRODUCTS_URL,
            payload=payload,
            timeout=timeout,
        )
    return ProductsResponse.model_validate(result)


async def get_products_by_filters_async(
    product_type: str,
    client: Any | None = None,
    *,
    page_number: int = 1,
    page_size: int = 500,
    domain: str = "uk",
    timeout: float = 30,
) -> ProductsResponse:
    """Asynchronously return one page of IB products for a product type."""
    if not product_type:
        raise ValueError("product_type must not be empty")
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    payload = ProductsByFiltersRequest(
        domain=domain,
        page_number=page_number,
        page_size=page_size,
        product_type=[product_type],
    ).model_dump(by_alias=True)
    async with _using_async_client(client) as active_client:
        result = await _request_json_async(
            active_client,
            "POST",
            _PRODUCTS_URL,
            payload=payload,
            timeout=timeout,
        )
    return ProductsResponse.model_validate(result)


def scrape_instruments(
    client: Any | None = None,
    *,
    domain: str = "uk",
    page_size: int = 500,
    timeout: float = 30,
) -> Generator[Instrument]:
    """Yield instruments, fetching every page reported by the type summary."""
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    with _using_client(client) as active_client:
        summary = get_instrument_summary(
            active_client,
            domain=domain,
            timeout=timeout,
        )
        for product_type, total_count in _product_totals(summary).items():
            page_count = (total_count + page_size - 1) // page_size
            for page_number in range(1, page_count + 1):
                page = get_products_by_filters(
                    product_type,
                    active_client,
                    page_number=page_number,
                    page_size=page_size,
                    domain=domain,
                    timeout=timeout,
                )
                expected_count = min(
                    page_size,
                    total_count - (page_number - 1) * page_size,
                )
                yield from _validated_products(
                    page.products,
                    product_type=product_type,
                    page_number=page_number,
                    expected_count=expected_count,
                )


async def scrape_instruments_async(
    client: Any | None = None,
    *,
    domain: str = "uk",
    page_size: int = 500,
    timeout: float = 30,
) -> AsyncGenerator[Instrument]:
    """Asynchronously yield every instrument reported by the type summary."""
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    async with _using_async_client(client) as active_client:
        summary = await get_instrument_summary_async(
            active_client,
            domain=domain,
            timeout=timeout,
        )
        for product_type, total_count in _product_totals(summary).items():
            page_count = (total_count + page_size - 1) // page_size
            for page_number in range(1, page_count + 1):
                page = await get_products_by_filters_async(
                    product_type,
                    active_client,
                    page_number=page_number,
                    page_size=page_size,
                    domain=domain,
                    timeout=timeout,
                )
                expected_count = min(
                    page_size,
                    total_count - (page_number - 1) * page_size,
                )
                for product in _validated_products(
                    page.products,
                    product_type=product_type,
                    page_number=page_number,
                    expected_count=expected_count,
                ):
                    yield product
