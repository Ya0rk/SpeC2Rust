import json
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


# 记录翻译过程中的指标，包括耗时、请求次数等
class TranslationMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self._started_at_monotonic: Optional[float] = None
            self._ended_at_monotonic: Optional[float] = None
            self._started_at_wall: Optional[datetime] = None
            self._ended_at_wall: Optional[datetime] = None
            self._llm_request_count = 0

    def start(self):
        now = datetime.now()
        with self._lock:
            self._started_at_monotonic = time.monotonic()
            self._ended_at_monotonic = None
            self._started_at_wall = now
            self._ended_at_wall = None
            self._llm_request_count = 0

    def finish(self):
        now = datetime.now()
        with self._lock:
            if self._started_at_monotonic is None:
                self._started_at_monotonic = time.monotonic()
                self._started_at_wall = now
            self._ended_at_monotonic = time.monotonic()
            self._ended_at_wall = now

    def increment_llm_requests(self):
        with self._lock:
            self._llm_request_count += 1
            return self._llm_request_count

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            started_at_monotonic = self._started_at_monotonic
            ended_at_monotonic = self._ended_at_monotonic
            started_at_wall = self._started_at_wall
            ended_at_wall = self._ended_at_wall
            llm_request_count = self._llm_request_count

        if started_at_monotonic is None:
            elapsed_seconds = 0
        else:
            end_value = ended_at_monotonic if ended_at_monotonic is not None else time.monotonic()
            elapsed_seconds = max(0, int(end_value - started_at_monotonic))

        return {
            "started_at": started_at_wall.isoformat(timespec="seconds") if started_at_wall else None,
            "ended_at": ended_at_wall.isoformat(timespec="seconds") if ended_at_wall else None,
            "elapsed_seconds": elapsed_seconds,
            "llm_request_count": llm_request_count,
        }

    def save_to(self, output_path: str) -> str:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self.snapshot()
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        return str(path)


translation_metrics = TranslationMetrics()
