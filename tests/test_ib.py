import asyncio
from typing import Any

import pytest

from asof_store.ib import (
    _EXCHANGES_URL,
    _FUND_PRODUCTS_URL,
    _PRODUCTS_URL,
    _SUMMARY_URL,
    InstrumentScrapeItem,
    InstrumentScrapePage,
    InstrumentScrapeSummary,
    get_exchanges,
    get_exchanges_async,
    get_funds_by_filters,
    get_funds_by_filters_async,
    get_instrument_summary,
    get_instrument_summary_async,
    get_products_by_filters,
    get_products_by_filters_async,
    scrape_exchanges,
    scrape_exchanges_async,
    scrape_funds,
    scrape_funds_async,
    scrape_instruments,
    scrape_instruments_async,
)
from asof_store.ib_models import (
    Exchange,
    ExchangeResponse,
    Fund,
    FundProductsRequest,
    FundProductsResponse,
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


def _instrument_items(items: list[InstrumentScrapeItem]) -> list[Instrument]:
    return [item for item in items if isinstance(item, Instrument)]


def _fund_payload(total: int, start: int, count: int) -> dict[str, Any]:
    return {
        "total": str(total),
        "funds": [
            {
                "CONID": str(start + index),
                "SYMBOL": f"FUND{start + index}",
                "ISIN": f"US{start + index:010d}",
                "NAME": f"Fund {start + index}",
            }
            for index in range(count)
        ],
    }


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


def test_scrape_exchanges_yields_models() -> None:
    body = {
        "exchanges": [
            {
                "id": "LSE",
                "name": "London Stock Exchange",
                "country": "United Kingdom",
                "region": "Europe",
                "assets": "Stocks",
                "country_code": "GB",
            }
        ],
        "productTypeCount": {},
        "productCount": 1,
    }

    exchanges = list(
        scrape_exchanges(FakeClient(lambda method, url, payload: FakeResponse(body)))
    )

    assert exchanges == [
        Exchange(
            id="LSE",
            name="London Stock Exchange",
            country="United Kingdom",
            region="Europe",
            assets="Stocks",
            country_code="GB",
        )
    ]
    assert exchanges[0].primary_key == ("LSE", "GB")


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
        exchange_items = [
            exchange
            async for exchange in scrape_exchanges_async(
                AsyncFakeClient(
                    lambda method, url, payload: FakeResponse(
                        {
                            "exchanges": [
                                {
                                    "id": "LSE",
                                    "name": "London Stock Exchange",
                                    "country": "United Kingdom",
                                    "region": "Europe",
                                    "assets": "Stocks",
                                    "country_code": "GB",
                                }
                            ],
                            "productTypeCount": {},
                            "productCount": 1,
                        }
                    )
                )
            )
        ]
        summary_result = await get_instrument_summary_async(client)
        product_result = await get_products_by_filters_async(
            "STK",
            client,
            page_number=2,
            page_size=500,
            product_country=["US", "CA"],
            new_product="F",
            sort_field="currency",
        )
        assert isinstance(exchange_result, ExchangeResponse)
        assert isinstance(summary_result[0], InstrumentSummaryItem)
        assert isinstance(product_result, ProductsResponse)
        assert product_result.products[0].symbol == "ABC"
        assert exchange_result.product_count == 0
        assert exchange_items[0].primary_key == ("LSE", "GB")
        assert [request[1] for request in client.requests] == [
            _EXCHANGES_URL,
            _SUMMARY_URL,
            _PRODUCTS_URL,
        ]
        assert client.requests[0][2] == {"timeouts": {"total": 12}}
        assert client.requests[1][2]["payload"]["newProduct"] == "all"
        assert client.requests[2][2]["payload"]["productCountry"] == ["US", "CA"]
        assert client.requests[2][2]["payload"]["newProduct"] == "F"
        assert client.requests[2][2]["payload"]["sortField"] == "currency"

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
        sort_field="country",
        sort_direction="desc",
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
        "sortDirection": "desc",
        "sortField": "country",
    }


def test_products_request_applies_sort_direction() -> None:
    client = FakeClient(
        lambda method, url, payload: FakeResponse({"products": [{"symbol": "ABC"}]})
    )

    get_products_by_filters("STK", client, sort_direction="desc")

    assert client.requests[0][2]["payload"]["sortDirection"] == "desc"


def test_fund_request_model_serializes_ib_api_names() -> None:
    request = FundProductsRequest()

    assert request.model_dump(by_alias=True) == {
        "domain": "uk",
        "newProduct": "all",
        "pageNumber": 1,
        "pageSize": 100,
        "productCountry": [],
        "productSymbol": "",
        "productType": ["FUND"],
        "sortDirection": "asc",
        "sortField": "symbol",
        "residency": "",
        "family": "",
        "isFundRenamed": "",
        "txnFee": "",
        "iType": "",
        "minInvestment": "",
        "maxInvestment": "",
        "identifier": "",
        "accountType": "",
        "currency": "",
    }


def test_fund_endpoint_sends_special_request_and_parses_funds() -> None:
    response_body = {
        "total": "1",
        "funds": [
            {
                "INVEST_TYPE": "Stock",
                "CONID": "514483865",
                "ISIN": "AT0000708334",
                "SYMBOL": "000070833",
                "NAME": "Example fund",
                "UNRECOGNIZED_IB_FIELD": "retained",
            }
        ],
    }
    client = FakeClient(lambda method, url, payload: FakeResponse(response_body))

    response = get_funds_by_filters(
        client,
        page_number=2,
        page_size=200,
        product_country=["AT"],
        product_symbol="ERSTE",
        family="ERSTE",
        i_type="Stock",
    )

    assert isinstance(response, FundProductsResponse)
    assert response.total == 1
    fund = response.funds[0]
    assert isinstance(fund, Fund)
    assert fund.primary_key == 514483865
    assert fund.isin == "AT0000708334"
    assert fund.model_extra == {"UNRECOGNIZED_IB_FIELD": "retained"}
    assert client.requests[0][1] == _FUND_PRODUCTS_URL
    payload = client.requests[0][2]["payload"]
    assert payload["productType"] == ["FUND"]
    assert payload["pageNumber"] == 2
    assert payload["pageSize"] == 200
    assert payload["productCountry"] == ["AT"]
    assert payload["productSymbol"] == "ERSTE"
    assert payload["family"] == "ERSTE"
    assert payload["iType"] == "Stock"


def test_scrape_funds_fetches_reported_pages() -> None:
    requested_pages: list[int] = []
    completed_pages: list[tuple[int, int]] = []

    def respond(method: str, url: str, payload: Any) -> FakeResponse:
        requested_pages.append(payload["pageNumber"])
        start = (payload["pageNumber"] - 1) * 100 + 1
        count = min(100, 101 - (payload["pageNumber"] - 1) * 100)
        return FakeResponse(_fund_payload(101, start, count))

    funds = list(
        scrape_funds(
            FakeClient(respond),
            page_size=100,
            on_page=lambda page, count: completed_pages.append((page, count)),
        )
    )

    assert requested_pages == [1, 2]
    assert completed_pages == [(1, 100), (2, 1)]
    assert len(funds) == 101
    assert funds[0].primary_key == 1
    assert funds[-1].primary_key == 101


def test_fund_endpoint_async_wrapper_and_scraper() -> None:
    async def exercise() -> None:
        client = AsyncFakeClient(
            lambda method, url, payload: FakeResponse(_fund_payload(1, 42, 1))
        )
        response = await get_funds_by_filters_async(client)
        funds = [
            fund
            async for fund in scrape_funds_async(
                AsyncFakeClient(
                    lambda method, url, payload: FakeResponse(
                        _fund_payload(1, 42, 1)
                    )
                )
            )
        ]

        assert response.funds[0].conid == 42
        assert [fund.primary_key for fund in funds] == [42]
        assert client.requests[0][1] == _FUND_PRODUCTS_URL
        assert client.requests[0][2]["payload"]["productType"] == ["FUND"]

    asyncio.run(exercise())


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
        assert payload["sortField"] == "exchange_id"
        assert payload["sortDirection"] == "desc"
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
            sort_field="exchange_id",
            sort_direction="desc",
        )
    )

    assert isinstance(results[0], InstrumentScrapeSummary)
    assert results[0].event == "summary"
    assert [(item.product_type, item.total_count) for item in results[0].products] == [
        ("STK", 101)
    ]
    products = _instrument_items(results)
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
    pages = [item for item in results if isinstance(item, InstrumentScrapePage)]
    assert [item.page_number for item in pages] == [1, 2]
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
    assert all(
        request[2]["payload"]["sortField"] == "exchange_id"
        for request in client.requests[1:]
    )
    assert all(
        request[2]["payload"]["sortDirection"] == "desc"
        for request in client.requests[1:]
    )


def test_scrape_instruments_yields_summary_before_requesting_products() -> None:
    def respond(method: str, url: str, payload: Any) -> FakeResponse:
        if url == _SUMMARY_URL:
            return FakeResponse([{"productType": "STK", "totalCount": 1}])
        return FakeResponse({"products": [{"type": "STK"}]})

    client = FakeClient(respond)
    items = scrape_instruments(client, product_type=["STK"], page_size=100)

    summary = next(items)
    assert isinstance(summary, InstrumentScrapeSummary)
    assert [request[1] for request in client.requests] == [_SUMMARY_URL]

    instrument = next(items)
    assert isinstance(instrument, Instrument)
    assert [request[1] for request in client.requests] == [
        _SUMMARY_URL,
        _PRODUCTS_URL,
    ]
    items.close()


def test_scrape_instruments_can_start_from_selected_product_page() -> None:
    requested_pages: list[int] = []

    def respond(method: str, url: str, payload: Any) -> FakeResponse:
        if url == _SUMMARY_URL:
            return FakeResponse([{"productType": "STK", "totalCount": 201}])
        page_number = payload["pageNumber"]
        requested_pages.append(page_number)
        return FakeResponse(
            {
                "products": [
                    {"type": "STK", "page": page_number, "index": index}
                    for index in range(min(100, 201 - (page_number - 1) * 100))
                ]
            }
        )

    items = list(
        scrape_instruments(
            FakeClient(respond),
            page_size=100,
            product_type=["STK"],
            start_page_number=2,
        )
    )

    assert requested_pages == [2, 3]
    assert isinstance(items[0], InstrumentScrapeSummary)
    products = _instrument_items(items)
    pages = [product.page for product in products]  # ty: ignore[unresolved-attribute]
    assert pages == [2] * 100 + [3]


def test_scrape_instruments_limits_pages_inclusively() -> None:
    requested_pages: list[int] = []

    def respond(method: str, url: str, payload: Any) -> FakeResponse:
        if url == _SUMMARY_URL:
            return FakeResponse([{"productType": "STK", "totalCount": 401}])
        page_number = payload["pageNumber"]
        requested_pages.append(page_number)
        return FakeResponse(
            {
                "products": [
                    {"type": "STK", "page": page_number, "index": index}
                    for index in range(100)
                ]
            }
        )

    items = list(
        scrape_instruments(
            FakeClient(respond),
            page_size=100,
            product_type=["STK"],
            start_page_number=2,
            end_page_number=3,
        )
    )

    assert requested_pages == [2, 3]
    assert isinstance(items[0], InstrumentScrapeSummary)
    products = _instrument_items(items)
    assert len(products) == 200


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
        items = [
            product
            async for product in scrape_instruments_async(
                client,
                page_size=100,
                product_type=["STK"],
                product_country=["US", "CA"],
                new_product="F",
            )
        ]

        assert isinstance(items[0], InstrumentScrapeSummary)
        assert items[0].products[0].total_count == 101
        products = _instrument_items(items)
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


def test_scrape_instruments_async_yields_summary_before_requesting_products() -> None:
    async def exercise() -> None:
        def respond(method: str, url: str, payload: Any) -> FakeResponse:
            if url == _SUMMARY_URL:
                return FakeResponse([{"productType": "STK", "totalCount": 1}])
            return FakeResponse({"products": [{"type": "STK"}]})

        client = AsyncFakeClient(respond)
        items = scrape_instruments_async(
            client,
            product_type=["STK"],
            page_size=100,
        )

        summary = await anext(items)
        assert isinstance(summary, InstrumentScrapeSummary)
        assert [request[1] for request in client.requests] == [_SUMMARY_URL]

        instrument = await anext(items)
        assert isinstance(instrument, Instrument)
        assert [request[1] for request in client.requests] == [
            _SUMMARY_URL,
            _PRODUCTS_URL,
        ]
        await items.aclose()

    asyncio.run(exercise())


def test_scrape_instruments_async_can_start_from_selected_product_page() -> None:
    async def exercise() -> None:
        requested_pages: list[int] = []

        def respond(method: str, url: str, payload: Any) -> FakeResponse:
            if url == _SUMMARY_URL:
                return FakeResponse([{"productType": "STK", "totalCount": 201}])
            page_number = payload["pageNumber"]
            requested_pages.append(page_number)
            return FakeResponse(
                {
                    "products": [
                        {"type": "STK", "page": page_number, "index": index}
                        for index in range(min(100, 201 - (page_number - 1) * 100))
                    ]
                }
            )

        items = [
            product
            async for product in scrape_instruments_async(
                AsyncFakeClient(respond),
                page_size=100,
                product_type=["STK"],
                start_page_number=2,
            )
        ]

        assert requested_pages == [2, 3]
        assert isinstance(items[0], InstrumentScrapeSummary)
        products = _instrument_items(items)
        pages = [product.page for product in products]  # ty: ignore[unresolved-attribute]
        assert pages == [2] * 100 + [3]

    asyncio.run(exercise())


def test_scrape_instruments_async_limits_pages_inclusively() -> None:
    async def exercise() -> None:
        requested_pages: list[int] = []

        def respond(method: str, url: str, payload: Any) -> FakeResponse:
            if url == _SUMMARY_URL:
                return FakeResponse([{"productType": "STK", "totalCount": 401}])
            page_number = payload["pageNumber"]
            requested_pages.append(page_number)
            return FakeResponse(
                {
                    "products": [
                        {"type": "STK", "page": page_number, "index": index}
                        for index in range(100)
                    ]
                }
            )

        items = [
            product
            async for product in scrape_instruments_async(
                AsyncFakeClient(respond),
                page_size=100,
                product_type=["STK"],
                start_page_number=2,
                end_page_number=3,
            )
        ]

        assert requested_pages == [2, 3]
        assert isinstance(items[0], InstrumentScrapeSummary)
        products = _instrument_items(items)
        assert len(products) == 200

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("product_type", "start_page_number", "end_page_number", "error"),
    [
        (None, 2, None, "page number limits"),
        (["STK", "BOND"], 2, None, "page number limits"),
        (None, 1, 2, "page number limits"),
        (["STK"], 0, None, "start_page_number"),
        (["STK"], 1, 0, "end_page_number"),
        (["STK"], 3, 2, "end_page_number"),
    ],
)
def test_scrape_instruments_rejects_invalid_start_page(
    product_type: list[str] | None,
    start_page_number: int,
    end_page_number: int | None,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        list(
            scrape_instruments(
                FakeClient(lambda method, url, payload: FakeResponse([])),
                product_type=product_type,
                start_page_number=start_page_number,
                end_page_number=end_page_number,
            )
        )


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
