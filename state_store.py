import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, timezone

class StateStore:
    def __init__(self, path: str = "state.json"):
        self.path = path

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            return {"updated_at": None, "oi": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"updated_at": None, "oi": {}}

    def save(self, state: Dict[str, Any]) -> None:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def get_prev_oi(self, state: Dict[str, Any], symbol: str) -> Optional[float]:
        try:
            return float(state.get("oi", {}).get(symbol))
        except Exception:
            return None

    def set_oi(self, state: Dict[str, Any], symbol: str, oi: float) -> None:
        state.setdefault("oi", {})
        state["oi"][symbol] = oi
