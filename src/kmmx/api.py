from __future__ import annotations

import base64
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence


class ApiError(RuntimeError):
    def __init__(self, status: int, message: str, body: str = "") -> None:
        super().__init__("Kalshi API error %s: %s" % (status, message))
        self.status = status
        self.body = body


class KalshiSigner:
    def __init__(self, api_key_id: str, private_key_path: str) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
        except ImportError as exc:
            raise RuntimeError(
                "Live mode needs cryptography; install with: pip install -e '.[live]'"
            ) from exc
        self.api_key_id = api_key_id
        with Path(private_key_path).expanduser().open("rb") as handle:
            self._private_key = serialization.load_pem_private_key(handle.read(), password=None)

    @classmethod
    def from_environment(cls) -> "KalshiSigner":
        api_key_id = os.environ.get("KALSHI_API_KEY_ID", "")
        private_key_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH", "")
        if not api_key_id or not private_key_path:
            raise RuntimeError(
                "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH for authenticated requests"
            )
        return cls(api_key_id, private_key_path)

    def headers(self, method: str, path: str) -> Dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = str(int(time.time() * 1000))
        sign_path = path.split("?", 1)[0]
        message = (timestamp + method.upper() + sign_path).encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("ascii"),
        }


class KalshiClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 3,
        signer: Optional[KalshiSigner] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.signer = signer

    def request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
        authenticated: bool = False,
    ) -> Dict[str, Any]:
        method = method.upper()
        query = urllib.parse.urlencode(
            {key: value for key, value in (params or {}).items() if value not in (None, "")},
            doseq=True,
        )
        relative = path if path.startswith("/") else "/" + path
        url = self.base_url + relative + (("?" + query) if query else "")
        data = json.dumps(body).encode("utf-8") if body is not None else None

        for attempt in range(self.max_retries + 1):
            headers = {"Accept": "application/json", "User-Agent": "kmmx/0.1.0"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            if authenticated:
                if self.signer is None:
                    raise RuntimeError("This request requires Kalshi API credentials")
                full_path = urllib.parse.urlparse(self.base_url + relative).path
                headers.update(self.signer.headers(method, full_path))
            request = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raw = exc.read().decode("utf-8", errors="replace")
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if retryable and attempt < self.max_retries:
                    time.sleep(min(4.0, (2**attempt) * 0.2 + random.random() * 0.1))
                    continue
                try:
                    message = json.loads(raw).get("message") or json.loads(raw).get("error")
                except (ValueError, AttributeError):
                    message = raw or exc.reason
                raise ApiError(exc.code, str(message), raw) from exc
            except urllib.error.URLError as exc:
                if attempt < self.max_retries:
                    time.sleep(min(4.0, (2**attempt) * 0.2 + random.random() * 0.1))
                    continue
                raise ApiError(0, str(exc.reason)) from exc
        raise ApiError(0, "request failed after retries")

    def get_incentives(
        self, status: str = "active", incentive_type: str = "liquidity", limit: int = 1000
    ) -> List[Dict[str, Any]]:
        response = self.request(
            "GET",
            "/incentive_programs",
            params={"status": status, "type": incentive_type, "limit": limit},
        )
        return list(response.get("incentive_programs") or [])

    def get_market(self, ticker: str) -> Dict[str, Any]:
        return self.request("GET", "/markets/%s" % urllib.parse.quote(ticker, safe=""))["market"]

    def get_markets(
        self, tickers: Optional[Sequence[str]] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if tickers:
            params["tickers"] = ",".join(tickers)
        else:
            params.update({"status": "open", "mve_filter": "exclude"})
        return list(self.request("GET", "/markets", params=params).get("markets") or [])

    def get_orderbook(self, ticker: str, depth: int = 100) -> Dict[str, Any]:
        path = "/markets/%s/orderbook" % urllib.parse.quote(ticker, safe="")
        return self.request("GET", path, params={"depth": depth})

    def get_trades(
        self, ticker: str, min_ts: Optional[int] = None, limit: int = 1000
    ) -> List[Dict[str, Any]]:
        response = self.request(
            "GET",
            "/markets/trades",
            params={"ticker": ticker, "min_ts": min_ts, "limit": limit, "is_block_trade": False},
        )
        return list(response.get("trades") or [])

    def get_balance(self) -> Dict[str, Any]:
        return self.request("GET", "/portfolio/balance", authenticated=True)

    def get_positions(self, ticker: Optional[str] = None) -> List[Dict[str, Any]]:
        response = self.request(
            "GET",
            "/portfolio/positions",
            params={"ticker": ticker, "count_filter": "position", "limit": 1000},
            authenticated=True,
        )
        return list(response.get("market_positions") or [])

    def get_orders(
        self, ticker: Optional[str] = None, status: str = "resting"
    ) -> List[Dict[str, Any]]:
        response = self.request(
            "GET",
            "/portfolio/orders",
            params={"ticker": ticker, "status": status, "limit": 1000},
            authenticated=True,
        )
        return list(response.get("orders") or [])

    def batch_create_orders(self, orders: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        return self.request(
            "POST",
            "/portfolio/events/orders/batched",
            body={"orders": list(orders)},
            authenticated=True,
        )

    def batch_cancel_orders(self, order_ids: Sequence[str]) -> Dict[str, Any]:
        body = {"orders": [{"order_id": order_id, "subaccount": 0} for order_id in order_ids]}
        return self.request(
            "DELETE", "/portfolio/events/orders/batched", body=body, authenticated=True
        )

    def create_order_group(self, contracts_limit: int) -> str:
        response = self.request(
            "POST",
            "/portfolio/order_groups/create",
            body={"contracts_limit": contracts_limit, "subaccount": 0},
            authenticated=True,
        )
        return str(response["order_group_id"])

    def delete_order_group(self, order_group_id: str) -> Dict[str, Any]:
        path = "/portfolio/order_groups/%s" % urllib.parse.quote(order_group_id, safe="")
        return self.request("DELETE", path, authenticated=True)
