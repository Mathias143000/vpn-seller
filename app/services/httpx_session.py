from __future__ import annotations

from enum import Enum
from typing import Any, AsyncGenerator, Dict, Optional, cast

import httpx
from aiogram.client.bot import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import TelegramMethod
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods.base import TelegramType
from aiogram.types import InputFile


class HttpxSession(BaseSession):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client: httpx.AsyncClient | None = None

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: Optional[int] = None,
    ) -> TelegramType:
        client = self._get_client(timeout=timeout)
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        data, files = await self._build_request_payload(bot=bot, method=method)

        try:
            response = await client.post(
                url,
                data=data,
                files=files or None,
                timeout=self.timeout if timeout is None else timeout,
            )
        except httpx.TimeoutException:
            raise TelegramNetworkError(method=method, message="Request timeout error")
        except httpx.HTTPError as exc:
            raise TelegramNetworkError(method=method, message=f"{type(exc).__name__}: {exc}")

        checked = self.check_response(
            bot=bot,
            method=method,
            status_code=response.status_code,
            content=response.text,
        )
        return cast(TelegramType, checked.result)

    async def stream_content(
        self,
        url: str,
        headers: Optional[Dict[str, Any]] = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        client = self._get_client(timeout=timeout)
        try:
            async with client.stream("GET", url, headers=headers, timeout=timeout) as response:
                if raise_for_status:
                    response.raise_for_status()
                async for chunk in response.aiter_bytes(chunk_size=chunk_size):
                    yield chunk
        except httpx.TimeoutException:
            raise TelegramNetworkError(method="stream_content", message="Request timeout error")
        except httpx.HTTPError as exc:
            raise TelegramNetworkError(method="stream_content", message=f"{type(exc).__name__}: {exc}")

    def _get_client(self, *, timeout: Optional[int] = None) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout if timeout is None else timeout)
        return self._client

    async def _build_request_payload(
        self,
        *,
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> tuple[dict[str, Any], list[tuple[str, tuple[str, bytes]]]]:
        data: dict[str, Any] = {}
        pending_files: Dict[str, InputFile] = {}

        for key, value in method.model_dump(warnings=False).items():
            prepared = self.prepare_value(value, bot=bot, files=pending_files)
            if not prepared:
                continue
            if isinstance(prepared, Enum):
                prepared = prepared.value
            data[key] = prepared

        files: list[tuple[str, tuple[str, bytes]]] = []
        for key, value in pending_files.items():
            content = bytearray()
            async for chunk in value.read(bot):
                content.extend(chunk)
            files.append((key, (value.filename or key, bytes(content))))

        return data, files
