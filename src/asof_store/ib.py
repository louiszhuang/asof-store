from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

try:
    from zapros import AsyncClient, Client, RequestContext
except ImportError as exc:
    raise ImportError(
        "The IB integration requires Zapros; install the 'zapros' extra"
    ) from exc

_BASE_URL = "https://www.interactivebrokers.co.uk/webrest"
_EXCHANGES_URL = f"{_BASE_URL}/exchanges/"
_SUMMARY_URL = f"{_BASE_URL}/search/product-types/summary"
_PRODUCTS_URL = f"{_BASE_URL}/search/products-by-filters"
_PRODUCT_TYPES = (
    "CMDTY",
    "FOP",
    "IOPT",
    "IND",
    "FUND",
    "FUT",
    "CASH",
    "OPT",
    "ETF",
    "WAR",
    "BOND",
    "STK",
)


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


def _product_totals(summary: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = {}
    for item in summary:
        product_type = item.get("productType")
        total_count = item.get("totalCount")
        if (
            not isinstance(product_type, str)
            or not product_type
            or not isinstance(total_count, int)
            or isinstance(total_count, bool)
            or total_count < 0
        ):
            raise ValueError(f"Invalid IB instrument summary entry: {item!r}")
        if product_type in totals:
            raise ValueError(f"Duplicate IB instrument summary: {product_type}")
        totals[product_type] = total_count
    return totals


def _validated_products(
    products: list[Any],
    *,
    product_type: str,
    page_number: int,
    expected_count: int,
) -> list[dict[str, Any]]:
    if len(products) != expected_count:
        raise ValueError(
            f"Unexpected product count for {product_type} page "
            f"{page_number}: expected {expected_count}, received {len(products)}"
        )
    if any(not isinstance(product, dict) for product in products):
        raise TypeError(
            f"IB products must be objects for {product_type} page {page_number}"
        )
    return products


def _request_body(
    domain: str,
    page_number: int,
    page_size: int,
    product_types: tuple[str, ...] | list[str],
) -> dict[str, Any]:
    return {
        "domain": domain,
        "newProduct": "all",
        "pageNumber": page_number,
        "pageSize": page_size,
        "productCountry": [],
        "productSymbol": "",
        "productType": list(product_types),
        "sortDirection": "asc",
        "sortField": "symbol",
    }


def get_exchanges(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
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
    if not isinstance(result, dict) or not isinstance(result.get("exchanges"), list):
        raise TypeError("IB exchanges response must contain an exchanges list")
    return result


async def get_exchanges_async(
    client: Any | None = None,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
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
    if not isinstance(result, dict) or not isinstance(result.get("exchanges"), list):
        raise TypeError("IB exchanges response must contain an exchanges list")
    return result


def get_instrument_summary(
    client: Any | None = None,
    *,
    domain: str = "uk",
    timeout: float = 30,
) -> list[dict[str, Any]]:
    """Return the instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = _request_body(domain, 1, 100, _PRODUCT_TYPES)
    with _using_client(client) as active_client:
        result = _request_json(
            active_client,
            "POST",
            _SUMMARY_URL,
            payload=payload,
            timeout=timeout,
        )
    if not isinstance(result, list) or any(
        not isinstance(item, dict) for item in result
    ):
        raise TypeError("IB instrument summary response must be a list of objects")
    return result


async def get_instrument_summary_async(
    client: Any | None = None,
    *,
    domain: str = "uk",
    timeout: float = 30,
) -> list[dict[str, Any]]:
    """Asynchronously return instrument counts by IB product type."""
    _validate_timeout(timeout)
    payload = _request_body(domain, 1, 100, _PRODUCT_TYPES)
    async with _using_async_client(client) as active_client:
        result = await _request_json_async(
            active_client,
            "POST",
            _SUMMARY_URL,
            payload=payload,
            timeout=timeout,
        )
    if not isinstance(result, list) or any(
        not isinstance(item, dict) for item in result
    ):
        raise TypeError("IB instrument summary response must be a list of objects")
    return result


def get_products_by_filters(
    product_type: str,
    client: Any | None = None,
    *,
    page_number: int = 1,
    page_size: int = 500,
    domain: str = "uk",
    timeout: float = 30,
) -> dict[str, Any]:
    """Return one page of IB products for a product type."""
    if not product_type:
        raise ValueError("product_type must not be empty")
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    payload = _request_body(domain, page_number, page_size, [product_type])
    with _using_client(client) as active_client:
        result = _request_json(
            active_client,
            "POST",
            _PRODUCTS_URL,
            payload=payload,
            timeout=timeout,
        )
    if not isinstance(result, dict) or not isinstance(result.get("products"), list):
        raise TypeError("IB products response must contain a products list")
    return result


async def get_products_by_filters_async(
    product_type: str,
    client: Any | None = None,
    *,
    page_number: int = 1,
    page_size: int = 500,
    domain: str = "uk",
    timeout: float = 30,
) -> dict[str, Any]:
    """Asynchronously return one page of IB products for a product type."""
    if not product_type:
        raise ValueError("product_type must not be empty")
    if page_number < 1:
        raise ValueError("page_number must be at least 1")
    _validate_page_size(page_size)
    _validate_timeout(timeout)

    payload = _request_body(domain, page_number, page_size, [product_type])
    async with _using_async_client(client) as active_client:
        result = await _request_json_async(
            active_client,
            "POST",
            _PRODUCTS_URL,
            payload=payload,
            timeout=timeout,
        )
    if not isinstance(result, dict) or not isinstance(result.get("products"), list):
        raise TypeError("IB products response must contain a products list")
    return result


def scrape_instruments(
    client: Any | None = None,
    *,
    domain: str = "uk",
    page_size: int = 500,
    timeout: float = 30,
) -> Iterator[dict[str, Any]]:
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
                    page["products"],
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
) -> AsyncIterator[dict[str, Any]]:
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
                    page["products"],
                    product_type=product_type,
                    page_number=page_number,
                    expected_count=expected_count,
                ):
                    yield product
