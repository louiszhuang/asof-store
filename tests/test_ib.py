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
from asof_store.ib_models import (
    ExchangeResponse,
    Instrument,
    InstrumentSummaryItem,
    InstrumentSummaryRequest,
    ProductsByFiltersRequest,
    ProductsResponse,
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
    body = {
        "exchanges": [],
        "productTypeCount": {},
        "productCount": 1,
    }
    client = FakeClient(lambda method, url, payload: FakeResponse(body))

    exchanges = get_exchanges(client, timeout=12)
    assert isinstance(exchanges, ExchangeResponse)
    assert exchanges.product_count == 1
    assert client.requests == [("GET", _EXCHANGES_URL, {"timeouts": {"total": 12}})]


def test_async_endpoint_wrappers_call_ib_endpoints_directly() -> None:
    async def exercise() -> None:
        exchanges = {
            "exchanges": [],
            "productTypeCount": {},
            "productCount": 0,
        }
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

        exchange_result = await get_exchanges_async(client, timeout=12)
        summary_result = await get_instrument_summary_async(client)
        product_result = await get_products_by_filters_async(
            "STK",
            client,
            page_number=2,
            page_size=500,
            product_country=["US", "CA"],
            new_product="F",
        )
        assert isinstance(exchange_result, ExchangeResponse)
        assert isinstance(summary_result[0], InstrumentSummaryItem)
        assert isinstance(product_result, ProductsResponse)
        assert product_result.products[0].symbol == "ABC"
        assert exchange_result.product_count == 0
        assert [request[1] for request in client.requests] == [
            _EXCHANGES_URL,
            _SUMMARY_URL,
            _PRODUCTS_URL,
        ]
        assert client.requests[0][2] == {"timeouts": {"total": 12}}
        assert client.requests[1][2]["payload"]["newProduct"] == "all"
        assert client.requests[2][2]["payload"]["productCountry"] == ["US", "CA"]
        assert client.requests[2][2]["payload"]["newProduct"] == "F"

    asyncio.run(exercise())


def test_instrument_endpoint_wrappers_send_expected_bodies() -> None:
    summary = [{"productType": "STK", "totalCount": 1}]
    products = {"products": [{"symbol": "ABC"}]}
    client = FakeClient(
        lambda method, url, payload: FakeResponse(
            summary if url == _SUMMARY_URL else products
        )
    )

    summary_result = get_instrument_summary(client, new_product="T")
    product_result = get_products_by_filters(
        "STK",
        client,
        page_number=2,
        page_size=500,
        domain="uk",
        product_country=["US", "CA"],
        new_product="F",
    )
    assert summary_result == [InstrumentSummaryItem.model_validate(summary[0])]
    assert isinstance(product_result, ProductsResponse)
    assert product_result.products[0].symbol == "ABC"
    assert client.requests[0][1] == _SUMMARY_URL
    summary_payload = client.requests[0][2]["payload"]
    assert summary_payload["pageSize"] == 100
    assert summary_payload["newProduct"] == "T"
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
        "newProduct": "F",
        "pageNumber": 2,
        "pageSize": 500,
        "productCountry": ["US", "CA"],
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
            assert payload["productType"] == ["STK"]
            assert payload["productCountry"] == ["US", "CA"]
            assert payload["newProduct"] == "T"
            return FakeResponse(
                [
                    item
                    for item in summary
                    if item["productType"] in payload["productType"]
                ]
            )
        product_type = payload["productType"][0]
        page_number = payload["pageNumber"]
        assert payload["newProduct"] == "T"
        total_count = next(
            item["totalCount"]
            for item in summary
            if item["productType"] == product_type
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

    results = list(
        scrape_instruments(
            client,
            page_size=100,
            product_type=["STK"],
            product_country=["US", "CA"],
            new_product="T",
        )
    )

    assert len(results) == 101
    assert isinstance(results[0], Instrument)
    assert results[0].product_type == "STK"
    assert results[0].page == 1  # ty: ignore[unresolved-attribute]
    assert results[0].index == 0  # ty: ignore[unresolved-attribute]
    assert results[99].index == 99  # ty: ignore[unresolved-attribute]
    assert results[100].page == 2  # ty: ignore[unresolved-attribute]
    assert results[-1].product_type == "STK"
    assert results[-1].page == 2  # ty: ignore[unresolved-attribute]
    assert results[-1].index == 0  # ty: ignore[unresolved-attribute]
    assert len(client.requests) == 3
    assert all(
        request[2]["payload"]["productCountry"] == ["US", "CA"]
        for request in client.requests[1:]
    )
    assert all(
        request[2]["payload"]["newProduct"] == "T"
        for request in client.requests
        if request[1] in (_SUMMARY_URL, _PRODUCTS_URL)
    )


def test_scrape_instruments_async_fetches_every_reported_page() -> None:
    async def exercise() -> None:
        summary = [
            {"productType": "STK", "totalCount": 101},
            {"productType": "BOND", "totalCount": 1},
        ]

        def respond(method: str, url: str, payload: Any) -> FakeResponse:
            if url == _SUMMARY_URL:
                assert payload["productType"] == ["STK"]
                assert payload["productCountry"] == ["US", "CA"]
                assert payload["newProduct"] == "F"
                return FakeResponse(
                    [
                        item
                        for item in summary
                        if item["productType"] in payload["productType"]
                    ]
                )
            product_type = payload["productType"][0]
            assert payload["newProduct"] == "F"
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
            async for product in scrape_instruments_async(
                client,
                page_size=100,
                product_type=["STK"],
                product_country=["US", "CA"],
                new_product="F",
            )
        ]

        assert len(products) == 101
        assert isinstance(products[0], Instrument)
        assert products[0].product_type == "STK"
        assert products[0].page == 1  # ty: ignore[unresolved-attribute]
        assert products[0].index == 0  # ty: ignore[unresolved-attribute]
        assert products[99].index == 99  # ty: ignore[unresolved-attribute]
        assert products[100].page == 2  # ty: ignore[unresolved-attribute]
        assert products[-1].product_type == "STK"
        assert products[-1].page == 2  # ty: ignore[unresolved-attribute]
        assert products[-1].index == 0  # ty: ignore[unresolved-attribute]
        assert len(client.requests) == 3
        assert all(
            request[2]["payload"]["productCountry"] == ["US", "CA"]
            for request in client.requests[1:]
        )
        assert all(
            request[2]["payload"]["newProduct"] == "F"
            for request in client.requests
            if request[1] in (_SUMMARY_URL, _PRODUCTS_URL)
        )

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("product_type", "page_number", "page_size"),
    [
        ("", 1, 100),
        ("STK", 0, 100),
        ("STK", 1, 501),
        ("STK", 1, 150),
    ],
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


def test_products_request_model_serializes_ib_api_names() -> None:
    request = ProductsByFiltersRequest(
        domain="uk",
        page_number=2,
        page_size=200,
        product_type=["STK"],
    )

    assert request.model_dump(by_alias=True) == {
        "domain": "uk",
        "newProduct": "all",
        "pageNumber": 2,
        "pageSize": 200,
        "productCountry": [],
        "productSymbol": "",
        "productType": ["STK"],
        "sortDirection": "asc",
        "sortField": "symbol",
    }


def test_products_request_model_rejects_unsupported_page_size() -> None:
    with pytest.raises(ValueError):
        ProductsByFiltersRequest(product_type=["STK"], page_size=150)


@pytest.mark.parametrize("new_product", ["N", "true", ""])
def test_request_models_reject_unsupported_new_product(
    new_product: str,
) -> None:
    with pytest.raises(ValueError):
        ProductsByFiltersRequest(
            product_type=["STK"],
            new_product=new_product,  # ty: ignore[invalid-argument-type]
        )


@pytest.mark.parametrize(
    "summary_item",
    [
        {"productType": "STK", "totalCount": True},
        {"productType": "STK", "totalCount": -1},
    ],
)
def test_instrument_summary_model_rejects_invalid_counts(
    summary_item: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        InstrumentSummaryItem.model_validate(summary_item)


def test_instrument_summary_request_contains_supported_product_types() -> None:
    request = InstrumentSummaryRequest()

    assert request.model_dump(by_alias=True)["productType"] == [
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


def test_instrument_model_preserves_unmodeled_ib_fields() -> None:
    instrument = Instrument.model_validate(
        {"conid": 123, "symbol": "ABC", "vendorField": {"extra": True}}
    )

    assert instrument.conid == 123
    assert instrument.model_dump(by_alias=True, exclude_unset=True) == {
        "conid": 123,
        "symbol": "ABC",
        "vendorField": {"extra": True},
    }
    assert instrument.primary_key == 123
    assert Instrument.model_json_schema()["properties"]["conid"]["primary_key"]


def test_instrument_primary_key_is_missing_when_ib_omits_conid() -> None:
    instrument = Instrument.model_validate(
        {"type": "OPT", "symbol": "0BN", "conid": None, "fcConid": 1}
    )

    assert instrument.primary_key is None
