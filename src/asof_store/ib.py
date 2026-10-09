from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal

try:
    from pydantic import TypeAdapter
    from zapros import AsyncClient, Client, RequestContext
except ImportError as exc:
    raise ImportError(
        "The IB integration requires Pydantic and Zapros; install the 'scraper' extra"
    ) from exc

from .ib_models import (
    Exchange,
    ExchangeResponse,
    Instrument,
    InstrumentSummaryItem,
    InstrumentSummaryRequest,
    NewProduct,
    ProductsByFiltersRequest,
    ProductsResponse,
)

_SUMMARY_ADAPTER = TypeAdapter(list[InstrumentSummaryItem])

_BASE_URL = "https://www.interactivebrokers.co.uk/webrest"
_EXCHANGES_URL = f"{_BASE_URL}/exchanges/"
_SUMMARY_URL = f"{_BASE_URL}/search/product-types/summary"
_PRODUCTS_URL = f"{_BASE_URL}/search/products-by-filters"


@dataclass(frozen=True)
class InstrumentScrapeSummary:
    products: tuple[InstrumentSummaryItem, ...]
    event: Literal["summary"] = "summary"


type InstrumentScrapeItem = InstrumentScrapeSummary | Instrument


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


def scrape_exchanges(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> Generator[Exchange]:
    """Yield IB exchanges."""
    yield from get_exchanges(client, timeout=timeout).exchanges


async def scrape_exchanges_async(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> AsyncGenerator[Exchange]:
    """Asynchronously yield IB exchanges."""
    response = await get_exchanges_async(client, timeout=timeout)
    for exchange in response.exchanges:
        yield exchange


def get_instrument_summary(
    client: Any | None = None,
    *,
    domain: str = "uk",
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    timeout: float = 30,
) -> list[InstrumentSummaryItem]:
    """Return the instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = InstrumentSummaryRequest(
        domain=domain,
        new_product=new_product,
        product_type=(
            InstrumentSummaryRequest().product_type
            if product_type is None
            else product_type
        ),
        product_country=[] if product_country is None else product_country,
    ).model_dump(by_alias=True)
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
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    timeout: float = 30,
) -> list[InstrumentSummaryItem]:
    """Asynchronously return instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = InstrumentSummaryRequest(
        domain=domain,
        new_product=new_product,
        product_type=(
            InstrumentSummaryRequest().product_type
            if product_type is None
            else product_type
        ),
        product_country=[] if product_country is None else product_country,
    ).model_dump(by_alias=True)
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
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
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
        new_product=new_product,
        page_number=page_number,
        page_size=page_size,
        product_type=[product_type],
        product_country=[] if product_country is None else product_country,
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
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
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
        new_product=new_product,
        page_number=page_number,
        page_size=page_size,
        product_type=[product_type],
        product_country=[] if product_country is None else product_country,
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
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
) -> Generator[InstrumentScrapeItem]:
    """Yield a summary event, then instruments filtered by type and country."""
    if start_page_number < 1:
        raise ValueError("start_page_number must be at least 1")
    if end_page_number is not None and end_page_number < 1:
        raise ValueError("end_page_number must be at least 1")
    if end_page_number is not None and end_page_number < start_page_number:
        raise ValueError("end_page_number must be at least start_page_number")
    if (start_page_number != 1 or end_page_number is not None) and (
        product_type is None or len(product_type) != 1
    ):
        raise ValueError(
            "page number limits can be set only when one product_type is selected"
        )
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    with _using_client(client) as active_client:
        summary = get_instrument_summary(
            active_client,
            domain=domain,
            product_type=product_type,
            product_country=product_country,
            new_product=new_product,
            timeout=timeout,
        )
        totals = _product_totals(summary)
        yield InstrumentScrapeSummary(tuple(summary))
        for _product_type, total_count in totals.items():
            page_count = (total_count + page_size - 1) // page_size
            last_page_number = (
                page_count
                if end_page_number is None
                else min(page_count, end_page_number)
            )
            for page_number in range(start_page_number, last_page_number + 1):
                page = get_products_by_filters(
                    _product_type,
                    active_client,
                    page_number=page_number,
                    page_size=page_size,
                    domain=domain,
                    product_country=product_country,
                    new_product=new_product,
                    timeout=timeout,
                )
                expected_count = min(
                    page_size,
                    total_count - (page_number - 1) * page_size,
                )
                yield from _validated_products(
                    page.products,
                    product_type=_product_type,
                    page_number=page_number,
                    expected_count=expected_count,
                )


async def scrape_instruments_async(
    client: Any | None = None,
    *,
    domain: str = "uk",
    page_size: int = 500,
    product_type: list[str] | None = None,
    product_country: list[str] | None = None,
    new_product: NewProduct = "all",
    start_page_number: int = 1,
    end_page_number: int | None = None,
    timeout: float = 30,
) -> AsyncGenerator[InstrumentScrapeItem]:
    """Asynchronously yield a summary event, then filtered instruments."""
    if start_page_number < 1:
        raise ValueError("start_page_number must be at least 1")
    if end_page_number is not None and end_page_number < 1:
        raise ValueError("end_page_number must be at least 1")
    if end_page_number is not None and end_page_number < start_page_number:
        raise ValueError("end_page_number must be at least start_page_number")
    if (start_page_number != 1 or end_page_number is not None) and (
        product_type is None or len(product_type) != 1
    ):
        raise ValueError(
            "page number limits can be set only when one product_type is selected"
        )
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    async with _using_async_client(client) as active_client:
        summary = await get_instrument_summary_async(
            active_client,
            domain=domain,
            product_type=product_type,
            product_country=product_country,
            new_product=new_product,
            timeout=timeout,
        )
        totals = _product_totals(summary)
        yield InstrumentScrapeSummary(tuple(summary))
        for _product_type, total_count in totals.items():
            page_count = (total_count + page_size - 1) // page_size
            last_page_number = (
                page_count
                if end_page_number is None
                else min(page_count, end_page_number)
            )
            for page_number in range(start_page_number, last_page_number + 1):
                page = await get_products_by_filters_async(
                    _product_type,
                    active_client,
                    page_number=page_number,
                    page_size=page_size,
                    domain=domain,
                    product_country=product_country,
                    new_product=new_product,
                    timeout=timeout,
                )
                expected_count = min(
                    page_size,
                    total_count - (page_number - 1) * page_size,
                )
                for product in _validated_products(
                    page.products,
                    product_type=_product_type,
                    page_number=page_number,
                    expected_count=expected_count,
                ):
                    yield product
