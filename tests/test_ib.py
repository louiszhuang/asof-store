import asyncio
from typing import Any

import pytest

from asof_store.ib import (
    _EXCHANGES_URL,
    _PRODUCTS_URL,
    _SUMMARY_URL,
    get_exchanges,
    get_exchanges_async,
    get_instrument_summary,
    get_instrument_summary_async,
    get_products_by_filters,
    get_products_by_filters_async,
    scrape_instruments,
    scrape_instruments_async,
)


class FakeResponse:
    def __init__(self, body: Any, status: int = 200) -> None:
        self.json = body
        self.status = status


class FakeClient:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    def get(self, url: str, *, context: dict[str, Any]) -> FakeResponse:
        self.requests.append(("GET", url, context))
        return self.responder("GET", url, None)

    def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        context: dict[str, Any],
    ) -> FakeResponse:
        self.requests.append(("POST", url, {"payload": json, **context}))
        return self.responder("POST", url, json)


class AsyncFakeClient:
    def __init__(self, responder: Any) -> None:
        self.responder = responder
        self.requests: list[tuple[str, str, dict[str, Any]]] = []

    async def get(self, url: str, *, context: dict[str, Any]) -> FakeResponse:
        self.requests.append(("GET", url, context))
        return self.responder("GET", url, None)

    async def post(
        self,
        url: str,
        *,
        json: dict[str, Any],
        context: dict[str, Any],
    ) -> FakeResponse:
        self.requests.append(("POST", url, {"payload": json, **context}))
        return self.responder("POST", url, json)


def test_get_exchanges_calls_ib_endpoint_directly() -> None:
    body = {"exchanges": [{"id": "NYSE"}], "productCount": 1}
    client = FakeClient(lambda method, url, payload: FakeResponse(body))

    assert get_exchanges(client, timeout=12) == body
    assert client.requests == [("GET", _EXCHANGES_URL, {"timeouts": {"total": 12}})]


def test_async_endpoint_wrappers_call_ib_endpoints_directly() -> None:
    async def exercise() -> None:
        exchanges = {"exchanges": [{"id": "NYSE"}]}
        summary = [{"productType": "STK", "totalCount": 1}]
        products = {"products": [{"symbol": "ABC"}]}
        client = AsyncFakeClient(
            lambda method, url, payload: FakeResponse(
                exchanges
                if url == _EXCHANGES_URL
                else summary
                if url == _SUMMARY_URL
                else products
            )
        )

        assert await get_exchanges_async(client, timeout=12) == exchanges
        assert await get_instrument_summary_async(client) == summary
        assert (
            await get_products_by_filters_async(
                "STK",
                client,
                page_number=2,
                page_size=500,
            )
            == products
        )
        assert [request[1] for request in client.requests] == [
            _EXCHANGES_URL,
            _SUMMARY_URL,
            _PRODUCTS_URL,
        ]
        assert client.requests[0][2] == {"timeouts": {"total": 12}}

    asyncio.run(exercise())


def test_instrument_endpoint_wrappers_send_expected_bodies() -> None:
    summary = [{"productType": "STK", "totalCount": 1}]
    products = {"products": [{"symbol": "ABC"}]}
    client = FakeClient(
        lambda method, url, payload: FakeResponse(
            summary if url == _SUMMARY_URL else products
        )
    )

    assert get_instrument_summary(client) == summary
    assert (
        get_products_by_filters(
            "STK",
            client,
            page_number=2,
            page_size=500,
            domain="uk",
        )
        == products
    )
    assert client.requests[0][1] == _SUMMARY_URL
    summary_payload = client.requests[0][2]["payload"]
    assert summary_payload["pageSize"] == 100
    assert summary_payload["productType"] == [
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
    ]
    assert client.requests[1][1] == _PRODUCTS_URL
    assert client.requests[1][2]["payload"] == {
        "domain": "uk",
        "newProduct": "all",
        "pageNumber": 2,
        "pageSize": 500,
        "productCountry": [],
        "productSymbol": "",
        "productType": ["STK"],
        "sortDirection": "asc",
        "sortField": "symbol",
    }


def test_scrape_instruments_fetches_each_page_for_each_type() -> None:
    summary = [
        {"productType": "STK", "totalCount": 101},
        {"productType": "BOND", "totalCount": 1},
        {"productType": "FUT", "totalCount": 0},
    ]

    def respond(method: str, url: str, payload: Any) -> FakeResponse:
        if url == _SUMMARY_URL:
            return FakeResponse(summary)
        product_type = payload["productType"][0]
        page_number = payload["pageNumber"]
        total_count = next(
            item["totalCount"] for item in summary if item["productType"] == product_type
        )
        count = min(100, total_count - (page_number - 1) * 100)
        return FakeResponse(
            {
                "products": [
                    {"type": product_type, "page": page_number, "index": index}
                    for index in range(count)
                ]
            }
        )

    client = FakeClient(respond)

    results = list(scrape_instruments(client, page_size=100))

    assert len(results) == 102
    assert results[0] == {"type": "STK", "page": 1, "index": 0}
    assert results[99] == {"type": "STK", "page": 1, "index": 99}
    assert results[100] == {"type": "STK", "page": 2, "index": 0}
    assert results[-1] == {"type": "BOND", "page": 1, "index": 0}
    assert len(client.requests) == 4


def test_scrape_instruments_async_fetches_every_reported_page() -> None:
    async def exercise() -> None:
        summary = [
            {"productType": "STK", "totalCount": 101},
            {"productType": "BOND", "totalCount": 1},
        ]

        def respond(method: str, url: str, payload: Any) -> FakeResponse:
            if url == _SUMMARY_URL:
                return FakeResponse(summary)
            product_type = payload["productType"][0]
            total_count = next(
                item["totalCount"]
                for item in summary
                if item["productType"] == product_type
            )
            count = min(100, total_count - (payload["pageNumber"] - 1) * 100)
            return FakeResponse(
                {
                    "products": [
                        {
                            "type": product_type,
                            "page": payload["pageNumber"],
                            "index": index,
                        }
                        for index in range(count)
                    ]
                }
            )

        client = AsyncFakeClient(respond)
        products = [
            product
            async for product in scrape_instruments_async(client, page_size=100)
        ]

        assert len(products) == 102
        assert products[0] == {"type": "STK", "page": 1, "index": 0}
        assert products[99] == {"type": "STK", "page": 1, "index": 99}
        assert products[100] == {"type": "STK", "page": 2, "index": 0}
        assert products[-1] == {"type": "BOND", "page": 1, "index": 0}
        assert len(client.requests) == 4

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("product_type", "page_number", "page_size"),
    [("", 1, 100), ("STK", 0, 100), ("STK", 1, 501), ("STK", 1, 150)],
)
def test_get_products_rejects_invalid_pagination(
    product_type: str,
    page_number: int,
    page_size: int,
) -> None:
    with pytest.raises(ValueError):
        get_products_by_filters(
            product_type,
            FakeClient(lambda method, url, payload: FakeResponse({})),
            page_number=page_number,
            page_size=page_size,
        )
