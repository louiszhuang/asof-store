import json
from collections.abc import AsyncIterable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from zapros import (
    AsyncBaseHandler,
    AsyncBaseMiddleware,
    AsyncClosableStream,
    BaseHandler,
    BaseMiddleware,
    ClosableStream,
    Request,
    Response,
    UnhandledRequestError,
)

from ._abc import AsOfStoreABC


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return frozenset((key, _freeze_json(item)) for key, item in value.items())
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _sort_json_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _sort_json_keys(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_sort_json_keys(item) for item in value]
    return value


class RequestKey(dict[str, Any]):
    """Hashable dictionary key containing a URI and its parsed JSON body."""

    def __init__(self, *, uri: str, request_body: Any) -> None:
        super().__init__(
            uri=uri,
            request_body=_sort_json_keys(request_body),
        )

    def __hash__(self) -> int:
        return hash(_freeze_json(self))


MiddlewareMode = Literal["building", "as_of"]


class ZaprosAsOfMiddleware(AsyncBaseMiddleware, BaseMiddleware):
    """Record successful JSON responses or replay their latest as-of version."""

    def __init__(
        self,
        next_handler: AsyncBaseHandler | BaseHandler | None,
        store: AsOfStoreABC[datetime, RequestKey, dict],
        *,
        mode: MiddlewareMode,
        as_of: datetime | None = None,
    ) -> None:
        if mode not in ("building", "as_of"):
            raise ValueError(f"Unknown middleware mode: {mode!r}")
        if mode == "building" and next_handler is None:
            raise ValueError("building mode requires a next_handler")
        if mode == "as_of" and as_of is None:
            raise ValueError("as_of mode requires an as_of timestamp")
        if mode == "building" and as_of is not None:
            raise ValueError("as_of is only valid in as_of mode")
        if as_of is not None:
            if as_of.utcoffset() is None:
                raise ValueError("as_of must be timezone-aware")
            as_of = as_of.astimezone(UTC)

        self.next = cast(BaseHandler, next_handler)
        self.async_next = cast(AsyncBaseHandler, next_handler)
        self._next_handler = next_handler
        self._store = store
        self._mode = mode
        self._as_of = as_of
        self._last_recorded: dict[RequestKey, datetime] = {}

    @staticmethod
    def _copy_request(request: Request, body: bytes) -> Request:
        return Request(
            url=request.url,
            method=request.method,
            headers=request.headers,
            body=body,
            trailers=request.trailers,
            context=dict(request.context),  # ty: ignore[invalid-argument-type]
        )

    @staticmethod
    def _request_key(request: Request, body: bytes) -> RequestKey:
        request_body = json.loads(body) if body else None
        return RequestKey(uri=str(request.url), request_body=request_body)

    @staticmethod
    def _has_data(value: Any) -> bool:
        return value is not None and not (
            isinstance(value, (dict, list, str)) and len(value) == 0
        )

    @staticmethod
    def _read_json(body: bytes) -> Any | None:
        try:
            return json.loads(body)
        except json.JSONDecodeError, UnicodeDecodeError:
            return None

    @staticmethod
    def _copy_response(response: Response, body: bytes, request: Request) -> Response:
        headers = [
            (name, value)
            for name, value in response.headers.items()
            if name.lower()
            not in ("content-length", "content-encoding", "transfer-encoding")
        ]
        headers.append(("content-length", str(len(body))))
        return Response(
            status=response.status,
            headers=headers,
            content=body,
            context=dict(response.context),  # ty: ignore[invalid-argument-type]
            request=request,
        )

    def _record(self, key: RequestKey, data: dict[str, Any]) -> None:
        timestamp = datetime.now(UTC)
        previous = self._last_recorded.get(key)
        if previous is not None and timestamp <= previous:
            timestamp = previous + timedelta(microseconds=1)
        self._store.put(timestamp, key, data)
        self._last_recorded[key] = timestamp

    def _as_of_response(self, key: RequestKey, request: Request) -> Response:
        assert self._as_of is not None
        stored = self._store.get(self._as_of, key)
        if stored is None:
            raise UnhandledRequestError(
                f"No response exists at or before {self._as_of.isoformat()} "
                f"for {request.method} {request.url}"
            )
        return Response(status=200, json=stored, request=request)

    def handle(self, request: Request) -> Response:
        original_body = request.body
        if original_body is None:
            body = b""
        elif isinstance(original_body, bytes):
            body = original_body
        else:
            body = b"".join(cast(Iterable[bytes], original_body))
            if isinstance(original_body, ClosableStream):
                original_body.close()
        replayable_request = self._copy_request(request, body)
        key = self._request_key(replayable_request, body)

        if self._mode == "as_of":
            return self._as_of_response(key, replayable_request)

        assert self._next_handler is not None
        response = cast(BaseHandler, self._next_handler).handle(replayable_request)
        if not 200 <= response.status < 300:
            return response

        try:
            response_body = response.read()
            data = self._read_json(response_body)
            if isinstance(data, dict) and self._has_data(data):
                self._record(key, data)
        finally:
            response.close()
        return self._copy_response(response, response_body, replayable_request)

    async def ahandle(self, request: Request) -> Response:
        original_body = request.body
        if original_body is None:
            body = b""
        elif isinstance(original_body, bytes):
            body = original_body
        else:
            body = b"".join(
                [chunk async for chunk in cast(AsyncIterable[bytes], original_body)]
            )
            if isinstance(original_body, AsyncClosableStream):
                await original_body.aclose()
        replayable_request = self._copy_request(request, body)
        key = self._request_key(replayable_request, body)

        if self._mode == "as_of":
            return self._as_of_response(key, replayable_request)

        assert self._next_handler is not None
        response = await cast(AsyncBaseHandler, self._next_handler).ahandle(
            replayable_request
        )
        if not 200 <= response.status < 300:
            return response

        try:
            response_body = await response.aread()
            data = self._read_json(response_body)
            if isinstance(data, dict) and self._has_data(data):
                self._record(key, data)
        finally:
            await response.aclose()
        return self._copy_response(response, response_body, replayable_request)

    def close(self) -> None:
        handler = self._next_handler
        if handler is not None and isinstance(handler, BaseHandler):
            handler.close()

    async def aclose(self) -> None:
        handler = self._next_handler
        if handler is not None and isinstance(handler, AsyncBaseHandler):
            await handler.aclose()
