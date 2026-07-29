from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict


class DailyEquityStore:
    """Process-safe UTC-day equity baseline used by the live loss guard."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def get_or_create(self, current_equity: Decimal, now: datetime) -> Decimal:
        try:
            import fcntl
        except ImportError as exc:
            raise RuntimeError("live state locking is not supported on this platform") from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        day = now.astimezone(timezone.utc).date().isoformat()
        with self.lock_path.open("a+", encoding="utf-8") as lock_handle:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                payload: Dict[str, Any] = {}
                if self.path.exists():
                    try:
                        with self.path.open("r", encoding="utf-8") as handle:
                            payload = json.load(handle)
                    except (json.JSONDecodeError, OSError) as exc:
                        raise RuntimeError("live state file is unreadable: %s" % self.path) from exc
                baselines = payload.setdefault("daily_equity_baselines", {})
                if day not in baselines:
                    baselines[day] = str(current_equity)
                    payload["updated_at"] = now.astimezone(timezone.utc).isoformat()
                    temporary = self.path.with_name(
                        "%s.%s.tmp" % (self.path.name, uuid.uuid4().hex[:8])
                    )
                    with temporary.open("w", encoding="utf-8") as handle:
                        json.dump(payload, handle, indent=2, sort_keys=True)
                        handle.flush()
                        os.fsync(handle.fileno())
                    temporary.replace(self.path)
                return Decimal(str(baselines[day]))
            finally:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
