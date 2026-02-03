import os


import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Tuple
import httpx
from dotenv import load_dotenv

from binance_client import BinanceFuturesClient
from telegram_client import TelegramClient
from openai_summarizer import OpenAISummarizer
from indicators import ema, rsi
from state_store import StateStore

print("[debug] TELEGRAM_BOT_TOKEN exists?", "TELEGRAM_BOT_TOKEN" in os.environ)
print("[debug] available env sample:", sorted([k for k in os.environ.keys() if "TELEGRAM" in k or "OPENAI" in k or "BINANCE" in k])[:50])


load_dotenv()

def kst_now(offset_hours: int = 9) -> datetime:
    return datetime.now(timezone(timedelta(hours=offset_hours)))

def clamp_text_telegram(text: str, max_len: int = 4096) -> str:
    # Telegram sendMessage: 1-4096 chars after entities parsing
    if len(text) <= max_len:
        return text
    return text[: max_len - 20] + "\n…(truncated)"

def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def pick_top_bottom(rows: List[Dict[str, Any]], key: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rows2 = [r for r in rows if r.get(key) is not None]
    rows2.sort(key=lambda r: r[key])
    return rows2[-1], rows2[0]

def ensure_unique(picks: List[Dict[str, Any]], ranked_lists: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    picks: initial picks (may contain duplicates by symbol)
    ranked_lists: {name: sorted desc/asc lists we can draw replacements from}
    """
    used = set()
    out = []
    for p in picks:
        sym = p["symbol"]
        if sym not in used:
            out.append(p)
            used.add(sym)
        else:
            # replace using the relevant ranked list if provided
            # fallback: find next best from all lists
            replaced = None
            for _, lst in ranked_lists.items():
                for cand in lst:
                    if cand["symbol"] not in used:
                        replaced = cand
                        break
                if replaced:
                    break
            if replaced:
                out.append(replaced)
                used.add(replaced["symbol"])
            else:
                # keep duplicate if nothing else
                out.append(p)
    return out

async def build_report() -> str:
    # env
    BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com")
    TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
    TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
    OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
    OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")

    CONCURRENCY = int(os.getenv("CONCURRENCY", "20"))
    REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "12"))
    KST_OFFSET_HOURS = int(os.getenv("KST_OFFSET_HOURS", "9"))

    b = BinanceFuturesClient(BINANCE_BASE_URL, timeout_sec=REQUEST_TIMEOUT)
    tg = TelegramClient(TELEGRAM_BOT_TOKEN, timeout_sec=REQUEST_TIMEOUT)
    summarizer = OpenAISummarizer(OPENAI_API_KEY, OPENAI_MODEL)
    store = StateStore("state.json")
    state = store.load()

    sem = asyncio.Semaphore(CONCURRENCY)

    async def with_sem(coro):
        async with sem:
            return await coro

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        ex = await b.exchange_info(client)
        symbols = []
        for s in ex.get("symbols", []):
            # USDT-margined perpetuals
            if s.get("quoteAsset") == "USDT" and s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING":
                symbols.append(s["symbol"])

        tickers = await b.ticker_24hr_all(client)
        ticker_map: Dict[str, Dict[str, Any]] = {t["symbol"]: t for t in tickers if t.get("symbol")}

        async def compute_symbol_moves(symbol: str) -> Dict[str, Any]:
            kl = await with_sem(b.klines_1h(client, symbol=symbol, limit=25))
            # kline format:
            # [ openTime, open, high, low, close, volume, closeTime, quoteVolume, trades, ...]
            closes = [safe_float(x[4]) for x in kl if x and len(x) > 4]
            vols = [safe_float(x[5]) for x in kl if x and len(x) > 5]

            if len(closes) < 25:
                return {"symbol": symbol, "ret12": None, "ret24": None, "vol_ratio": None}

            last = closes[-1]
            c12 = closes[-13]
            c24 = closes[-25]

            ret12 = (last / c12 - 1.0) * 100.0 if (c12 and last) else None
            ret24 = (last / c24 - 1.0) * 100.0 if (c24 and last) else None

            # volume ratio: last hour vol vs mean of previous 12 hours
            vol_ratio = None
            if len(vols) >= 13 and vols[-1] is not None:
                base = [v for v in vols[-13:-1] if v is not None]
                if base:
                    avg = sum(base) / len(base)
                    if avg > 0:
                        vol_ratio = vols[-1] / avg

            return {"symbol": symbol, "ret12": ret12, "ret24": ret24, "vol_ratio": vol_ratio, "price": last}

        # compute returns for all symbols (1h klines, limit 25)
        tasks = [asyncio.create_task(compute_symbol_moves(sym)) for sym in symbols]
        moves = []
        for fut in asyncio.as_completed(tasks):
            try:
                moves.append(await fut)
            except Exception:
                pass

        # rank lists for replacement
        moves_ret12_desc = sorted([m for m in moves if m.get("ret12") is not None], key=lambda x: x["ret12"], reverse=True)
        moves_ret12_asc  = sorted([m for m in moves if m.get("ret12") is not None], key=lambda x: x["ret12"])
        moves_ret24_desc = sorted([m for m in moves if m.get("ret24") is not None], key=lambda x: x["ret24"], reverse=True)
        moves_ret24_asc  = sorted([m for m in moves if m.get("ret24") is not None], key=lambda x: x["ret24"])

        top12 = moves_ret12_desc[0]
        bot12 = moves_ret12_asc[0]
        top24 = moves_ret24_desc[0]
        bot24 = moves_ret24_asc[0]

        picks = [
            {**top12, "bucket": "12H_UP"},
            {**bot12, "bucket": "12H_DOWN"},
            {**top24, "bucket": "24H_UP"},
            {**bot24, "bucket": "24H_DOWN"},
        ]

        picks = ensure_unique(
            picks,
            ranked_lists={
                "ret12_desc": moves_ret12_desc,
                "ret12_asc": moves_ret12_asc,
                "ret24_desc": moves_ret24_desc,
                "ret24_asc": moves_ret24_asc,
            },
        )

        # enrich selected 4 symbols
        async def enrich(p: Dict[str, Any]) -> Dict[str, Any]:
            sym = p["symbol"]
            t = ticker_map.get(sym, {})

            # 1h klines for indicators (RSI, EMA50)
            kl = await with_sem(b.klines_1h(client, symbol=sym, limit=200))
            closes = [safe_float(x[4]) for x in kl if x and len(x) > 4]
            price = safe_float(t.get("lastPrice")) or (closes[-1] if closes else p.get("price"))

            ema50 = ema([c for c in closes if c is not None], 50) if closes else None
            rsi14 = rsi([c for c in closes if c is not None], 14) if closes else None

            oi_obj = await with_sem(b.open_interest(client, sym))
            oi = safe_float(oi_obj.get("openInterest"))

            prem = await with_sem(b.premium_index(client, sym))
            funding = safe_float(prem.get("lastFundingRate"))
            mark_price = safe_float(prem.get("markPrice"))

            prev_oi = store.get_prev_oi(state, sym)
            oi_chg_pct = None
            if oi is not None and prev_oi is not None and prev_oi > 0:
                oi_chg_pct = (oi / prev_oi - 1.0) * 100.0

            # update state (save later)
            if oi is not None:
                store.set_oi(state, sym, oi)

            quote_vol = safe_float(t.get("quoteVolume"))
            pct_24hr_ticker = safe_float(t.get("priceChangePercent"))

            return {
                **p,
                "price": price,
                "quote_vol": quote_vol,
                "ticker_24h_pct": pct_24hr_ticker,
                "ema50": ema50,
                "rsi": rsi14,
                "oi": oi,
                "oi_chg_pct": oi_chg_pct,
                "funding": funding,
                "mark_price": mark_price,
            }

        enriched = []
        for fut in asyncio.as_completed([asyncio.create_task(enrich(p)) for p in picks]):
            try:
                enriched.append(await fut)
            except Exception:
                pass

        # stable ordering in final message
        order = {"12H_UP": 0, "12H_DOWN": 1, "24H_UP": 2, "24H_DOWN": 3}
        enriched.sort(key=lambda x: order.get(x.get("bucket", ""), 99))

        # OpenAI short reasons (1 call)
        # Keep prompt minimal → cheap, less hallucination surface
        ai_input = []
        for it in enriched:
            ai_input.append({
                "symbol": it["symbol"],
                "bucket": it["bucket"],
                "ret12": it.get("ret12"),
                "ret24": it.get("ret24"),
                "vol_ratio": it.get("vol_ratio"),
                "quote_vol": it.get("quote_vol"),
                "oi": it.get("oi"),
                "oi_chg_pct": it.get("oi_chg_pct"),
                "funding": it.get("funding"),
                "rsi": it.get("rsi"),
                "price_vs_ema50": (None if (it.get("price") is None or it.get("ema50") is None) else (it["price"] - it["ema50"])),
            })

        reasons_map = summarizer.summarize(ai_input)

        # build telegram text
        now = kst_now(KST_OFFSET_HOURS)
        header = f"📊 Binance USDT 선물 변동 리포트\n(KST {now:%Y-%m-%d %H:%M})\n"
        lines = [header]

        def fmt_pct(x):
            return "NA" if x is None else f"{x:+.2f}%"

        def fmt_num(x):
            if x is None:
                return "NA"
            # large numbers compact
            if x >= 1e9:
                return f"{x/1e9:.2f}B"
            if x >= 1e6:
                return f"{x/1e6:.2f}M"
            if x >= 1e3:
                return f"{x/1e3:.2f}K"
            return f"{x:.2f}"

        # sections
        bucket_titles = {
            "12H_UP": "⏱ 12H (최근 12시간)",
            "12H_DOWN": "⏱ 12H (최근 12시간)",
            "24H_UP": "🗓 1D (최근 24시간)",
            "24H_DOWN": "🗓 1D (최근 24시간)",
        }

        # group by top header
        lines.append("—" * 28)
        lines.append("⏱ 12H (최근 12시간)")
        for it in enriched:
            if it["bucket"] not in ("12H_UP", "12H_DOWN"):
                continue
            emoji = "🟢" if it["bucket"] == "12H_UP" else "🔴"
            sym = it["symbol"]
            r = reasons_map.get(sym, ["", ""])
            r1 = r[0] if len(r) > 0 else ""
            r2 = r[1] if len(r) > 1 else ""
            line1 = (
                f"{emoji} {sym}  12H {fmt_pct(it.get('ret12'))} | 24H {fmt_pct(it.get('ret24'))}\n"
                f"   가격 {fmt_num(it.get('price'))} | RSI {('NA' if it.get('rsi') is None else f'{it['rsi']:.1f}')}"
                f" | 펀딩 {('NA' if it.get('funding') is None else f'{it['funding']:+.5f}')}\n"
                f"   OI {fmt_num(it.get('oi'))} ({('NA' if it.get('oi_chg_pct') is None else f'{it['oi_chg_pct']:+.1f}%')})"
                f" | 거래량배수 {('NA' if it.get('vol_ratio') is None else f'{it['vol_ratio']:.2f}x')}\n"
                f"   - {r1}\n"
                f"   - {r2}"
            )
            lines.append(line1)

        lines.append("—" * 28)
        lines.append("🗓 1D (최근 24시간)")
        for it in enriched:
            if it["bucket"] not in ("24H_UP", "24H_DOWN"):
                continue
            emoji = "🟢" if it["bucket"] == "24H_UP" else "🔴"
            sym = it["symbol"]
            r = reasons_map.get(sym, ["", ""])
            r1 = r[0] if len(r) > 0 else ""
            r2 = r[1] if len(r) > 1 else ""
            line1 = (
                f"{emoji} {sym}  24H {fmt_pct(it.get('ret24'))} | 12H {fmt_pct(it.get('ret12'))}\n"
                f"   가격 {fmt_num(it.get('price'))} | RSI {('NA' if it.get('rsi') is None else f'{it['rsi']:.1f}')}"
                f" | 펀딩 {('NA' if it.get('funding') is None else f'{it['funding']:+.5f}')}\n"
                f"   OI {fmt_num(it.get('oi'))} ({('NA' if it.get('oi_chg_pct') is None else f'{it['oi_chg_pct']:+.1f}%')})"
                f" | 거래량배수 {('NA' if it.get('vol_ratio') is None else f'{it['vol_ratio']:.2f}x')}\n"
                f"   - {r1}\n"
                f"   - {r2}"
            )
            lines.append(line1)

        text = "\n".join(lines)
        text = clamp_text_telegram(text, 4096)

        # persist state
        store.save(state)

        # send
        await tg.send_message(client, TELEGRAM_CHAT_ID, text)
        return text

async def run_loop():
    # schedule config
    KST_OFFSET_HOURS = int(os.getenv("KST_OFFSET_HOURS", "9"))
    at_min = int(os.getenv("SCHEDULE_AT_MINUTE", "0"))
    at_sec = int(os.getenv("SCHEDULE_AT_SECOND", "5"))

    # 1) immediate send
    try:
        await build_report()
    except Exception as e:
        # if first send fails, still continue loop
        print("[first send failed]", repr(e))

    # 2) every hour
    while True:
        now = kst_now(KST_OFFSET_HOURS)
        # next hour boundary
        nxt = (now + timedelta(hours=1)).replace(minute=at_min, second=at_sec, microsecond=0)
        sleep_s = max(1.0, (nxt - now).total_seconds())
        print(f"[sleep] {sleep_s:.1f}s until {nxt.isoformat()}")
        await asyncio.sleep(sleep_s)
        try:
            await build_report()
        except Exception as e:
            print("[scheduled send failed]", repr(e))

if __name__ == "__main__":
    asyncio.run(run_loop())
