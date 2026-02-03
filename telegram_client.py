import httpx
from typing import Optional, Dict, Any

class TelegramClient:
    def __init__(self, bot_token: str, timeout_sec: int = 12):
        self.bot_token = bot_token
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self.timeout = httpx.Timeout(timeout_sec)

    async def send_message(
        self,
        client: httpx.AsyncClient,
        chat_id: str,
        text: str,
        disable_web_page_preview: bool = True,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_web_page_preview,
        }
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()

    async def get_updates(self, client: httpx.AsyncClient, offset: Optional[int] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/getUpdates"
        params = {}
        if offset is not None:
            params["offset"] = offset
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()
