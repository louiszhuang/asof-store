import asyncio
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("zapros")

from zapros import (
    URL,
    Client,
    Request,
    Response,
    UnhandledRequestError,
)

from asof_store import AsOfStore
from asof_store.zapros import RequestKey, ZaprosAsOfMiddleware


class SyncHandler:
    def __init__(self, status: int = 200, json: object = None) -> None:
        self.status = status
        self.json = json
        self.requests: list[Request] = []

    def handle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(status=self.status, json=self.json)

    def close(self) -> None:
        pass


class AsyncHandler:
    def __init__(self, status: int = 200, json: object = None) -> None:
        self.status = status
        self.json = json
        self.requests: list[Request] = []

    async def ahandle(self, request: Request) -> Response:
        self.requests.append(request)
        return Response(status=self.status, json=self.json)

    async def aclose(self) -> None:
        pass


def make_request(body: object | None = None) -> Request:
    return Request(
        URL("https://api.example.com/items"),
        "POST",
        json=body,
    )


def test_building_records_successful_nonempty_json_by_url_and_body() -> None:
    store = AsOfStore.from_memory()
    handler = SyncHandler(json={"items": [1]})
    middleware = ZaprosAsOfMiddleware(handler, store, mode="building")  # ty: ignore[invalid-argument-type]
    request = make_request({"filter": "active"})

    response = middleware.handle(request)

    response.read()
    assert response.json == {"items": [1]}
    assert handler.requests[0].body == request.body
    key = RequestKey(uri=str(request.url), request_body={"filter": "active"})
    saved = store.get(datetime.now(UTC) + timedelta(seconds=1), key)
    assert saved == {"items": [1]}
    with pytest.raises(UnhandledRequestError):
        ZaprosAsOfMiddleware(
            None,
            store,
            mode="as_of",
            as_of=datetime.now(UTC) + timedelta(seconds=1),
        ).handle(make_request({"filter": "inactive"}))


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (503, {"items": [1]}),
        (200, {}),
        (200, []),
        (200, ""),
        (200, None),
        (200, [1]),
        (200, "value"),
        (200, False),
        (200, 0),
    ],
)
def test_building_skips_unsuccessful_or_empty_json(
    status: int,
    payload: object,
) -> None:
    store = AsOfStore.from_memory()
    middleware = ZaprosAsOfMiddleware(
        SyncHandler(status=status, json=payload),  # ty: ignore[invalid-argument-type]
        store,
        mode="building",
    )
    request = make_request()

    middleware.handle(request)

    with pytest.raises(UnhandledRequestError):
        ZaprosAsOfMiddleware(
            None,
            store,
            mode="as_of",
            as_of=datetime.now(UTC) + timedelta(seconds=1),
        ).handle(request)


def test_as_of_returns_latest_response_not_after_timestamp() -> None:
    store = AsOfStore.from_memory()
    key = RequestKey(uri="https://api.example.com/items", request_body=None)
    first = datetime(2025, 1, 1, tzinfo=UTC)
    second = datetime(2025, 1, 2, tzinfo=UTC)
    store.put(first, key, {"version": 1})
    store.put(second, key, {"version": 2})
    middleware = ZaprosAsOfMiddleware(
        None,
        store,
        mode="as_of",
        as_of=first + timedelta(hours=12),
    )

    response = middleware.handle(make_request())

    assert response.status == 200
    assert response.json == {"version": 1}


def test_as_of_raises_for_missing_data() -> None:
    middleware = ZaprosAsOfMiddleware(
        None,
        AsOfStore.from_memory(),
        mode="as_of",
        as_of=datetime.now(UTC),
    )

    with pytest.raises(UnhandledRequestError):
        middleware.handle(make_request())


def test_sync_zapros_client_uses_as_of_middleware() -> None:
    store = AsOfStore.from_memory()
    url = "https://api.example.com/items"
    as_of = datetime(2025, 1, 1, tzinfo=UTC)
    store.put(
        as_of,
        RequestKey(uri=url, request_body=None),
        {"items": [3]},
    )
    middleware = ZaprosAsOfMiddleware(None, store, mode="as_of", as_of=as_of)

    with Client(handler=middleware) as client:
        response = client.get(url)

    assert response.json == {"items": [3]}


def test_request_key_supports_sqlite_storage() -> None:
    pytest.importorskip("sqlalchemy")
    store = AsOfStore.from_sql(
        "sqlite:///:memory:",
        "zapros_versions",
        datetime,
        RequestKey,
        dict,
    )
    key_a = RequestKey(
        uri="https://api.example.com/items",
        request_body={"filters": {"state": "active", "kind": "book"}},
    )
    key_b = RequestKey(
        uri="https://api.example.com/items",
        request_body={"filters": {"kind": "book", "state": "active"}},
    )
    timestamp = datetime.now(UTC)

    try:
        assert key_a == key_b
        assert hash(key_a) == hash(key_b)
        assert store.put(timestamp, key_a, {"items": [1]})
        assert store.get(timestamp, key_b) == {"items": [1]}
    finally:
        store.close()


def test_async_building_and_as_of_round_trip() -> None:
    async def exercise() -> None:
        store = AsOfStore.from_memory()
        handler = AsyncHandler(json={"items": [2]})
        middleware = ZaprosAsOfMiddleware(handler, store, mode="building")  # ty: ignore[invalid-argument-type]
        request = make_request({"filter": "new"})

        response = await middleware.ahandle(request)
        replay = await ZaprosAsOfMiddleware(
            None,
            store,
            mode="as_of",
            as_of=datetime.now(UTC) + timedelta(seconds=1),
        ).ahandle(request)

        await response.aread()
        await replay.aread()
        assert response.json == {"items": [2]}
        assert replay.json == {"items": [2]}
        assert handler.requests[0].body == request.body

    asyncio.run(exercise())
