import os
import asyncio
import httpx
from dotenv import load_dotenv
from telegram_client import TelegramClient

load_dotenv()

async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    tg = TelegramClient(token)

    async with httpx.AsyncClient(timeout=15) as client:
        data = await tg.get_updates(client)
        print(data)

        # 사람이 보기 쉽게 chat_id만 추출
        results = data.get("result", [])
        chat_ids = set()
        for u in results:
            msg = u.get("message") or u.get("channel_post")
            if msg and msg.get("chat") and "id" in msg["chat"]:
                chat_ids.add(msg["chat"]["id"])
        print("chat_ids:", list(chat_ids))

if __name__ == "__main__":
    asyncio.run(main())
