import httpx
from typing import Any, Dict, List, Optional

class BinanceFuturesClient:
    def __init__(self, base_url: str, timeout_sec: int = 12):
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_sec)

    async def _get(self, client: httpx.AsyncClient, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

    async def exchange_info(self, client: httpx.AsyncClient) -> Dict[str, Any]:
        return await self._get(client, "/fapi/v1/exchangeInfo")

    async def ticker_24hr_all(self, client: httpx.AsyncClient) -> List[Dict[str, Any]]:
        # Weight: 40 if symbol omitted
        return await self._get(client, "/fapi/v1/ticker/24hr")

    async def klines_1h(self, client: httpx.AsyncClient, symbol: str, limit: int = 25) -> List[List[Any]]:
        # GET /fapi/v1/klines
        return await self._get(
            client,
            "/fapi/v1/klines",
            params={"symbol": symbol, "interval": "1h", "limit": limit},
        )

    async def open_interest(self, client: httpx.AsyncClient, symbol: str) -> Dict[str, Any]:
        return await self._get(client, "/fapi/v1/openInterest", params={"symbol": symbol})

    async def premium_index(self, client: httpx.AsyncClient, symbol: str) -> Dict[str, Any]:
        return await self._get(client, "/fapi/v1/premiumIndex", params={"symbol": symbol})
