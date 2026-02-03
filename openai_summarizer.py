import json
from typing import Dict, Any, List, Optional
from openai import OpenAI

class OpenAISummarizer:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def summarize(self, items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        """
        Returns: { "SYMBOL": ["요인1", "요인2"], ... }
        - '뉴스/이벤트'를 지어내지 않게 강하게 제한 (숫자 기반)
        """
        instructions = (
            "너는 암호화폐 선물 시장 리포터다. "
            "절대 '업그레이드/파트너십/상장/루머' 같은 이벤트를 만들어내지 마라. "
            "오직 제공된 수치(수익률, 거래대금, 펀딩, OI, RSI, EMA)에서만 해석해라. "
            "각 코인마다 한국어로 2개 요인을 매우 짧게(각 35자 이내) 써라. "
            "반드시 JSON만 출력해라. 코드블록 금지."
        )

        payload = {
            "items": items
        }

        resp = self.client.responses.create(
            model=self.model,
            instructions=instructions,
            input=json.dumps(payload, ensure_ascii=False),
        )

        text = (resp.output_text or "").strip()
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                # normalize
                out: Dict[str, List[str]] = {}
                for k, v in obj.items():
                    if isinstance(v, list):
                        out[k] = [str(x)[:60] for x in v[:2]]
                    else:
                        out[k] = [str(v)[:60]]
                return out
        except Exception:
            pass

        # fallback: no hallucination, rule-based
        fallback: Dict[str, List[str]] = {}
        for it in items:
            sym = it["symbol"]
            reasons = []
            if it.get("vol_ratio") is not None and it["vol_ratio"] >= 2.0:
                reasons.append("거래량 급증(단기 쏠림)")
            if it.get("oi_chg_pct") is not None and it["oi_chg_pct"] >= 3.0:
                reasons.append("OI 증가(포지션 유입)")
            if it.get("rsi") is not None:
                if it["rsi"] >= 70:
                    reasons.append("RSI 과열(추격매수 구간)")
                elif it["rsi"] <= 30:
                    reasons.append("RSI 과매도(반등/추가하락 분기)")
            if it.get("ema50") is not None and it.get("price") is not None:
                if it["price"] >= it["ema50"]:
                    reasons.append("EMA50 상단(추세 유지)")
                else:
                    reasons.append("EMA50 하단(추세 약세)")
            if it.get("funding") is not None:
                if it["funding"] > 0:
                    reasons.append("펀딩+(롱 우위)")
                elif it["funding"] < 0:
                    reasons.append("펀딩-(숏 우위)")

            # keep 2
            fallback[sym] = reasons[:2] if len(reasons) >= 2 else (reasons + ["모멘텀 주도 구간"])[:2]
        return fallback
