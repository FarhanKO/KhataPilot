"""HTTP client for the sandbox provider. The backend reaches the simulated wallet, bank, messaging
gateway and lender only through this class, exactly as it would reach real providers.

Side-effecting calls take a guard authorization header and an idempotency key; the client does
not create either. Simulator controls (reset, seed, clock) use the admin key and exist for
scripts and the demo's attack simulator only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx


class SandboxError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


class SandboxClient:
    def __init__(self, base_url: str, *, admin_key: str = "", http: httpx.Client | None = None,
                 timeout: float = 10.0) -> None:
        self._http = http or httpx.Client(base_url=base_url, timeout=timeout)
        self._admin_key = admin_key

    def close(self) -> None:
        self._http.close()

    # ---- plumbing

    def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> dict:
        response = self._http.request(method, path, headers=headers, **kwargs)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            detail = body.get("detail") if isinstance(body, dict) else None
            if isinstance(detail, dict):
                raise SandboxError(response.status_code, detail.get("code", "ERROR"), detail.get("message", ""))
            raise SandboxError(response.status_code, "HTTP_ERROR", str(detail or response.text)[:300])
        return body

    def _admin(self) -> dict[str, str]:
        return {"X-Admin-Key": self._admin_key}

    @staticmethod
    def _authorized(authorization: str, idempotency_key: str) -> dict[str, str]:
        return {"X-Guard-Authorization": authorization, "Idempotency-Key": idempotency_key}

    # ---- reads

    def health(self) -> dict:
        return self._request("GET", "/health")

    def account(self, account_id: str) -> dict:
        return self._request("GET", f"/v1/accounts/{account_id}")

    def accounts(self, holder: str | None = None) -> list[dict]:
        params = {"holder": holder} if holder else None
        return self._request("GET", "/v1/accounts", params=params)["accounts"]

    def statement(self, account_id: str, limit: int = 50) -> list[dict]:
        return self._request("GET", f"/v1/accounts/{account_id}/transactions", params={"limit": limit})["transactions"]

    def find_transaction(self, account_id: str, trx_id: str) -> dict | None:
        """The provider's record of one transaction on an account, or None if it has no such TrxID."""
        try:
            return self._request("GET", f"/v1/accounts/{account_id}/transactions/{trx_id}")
        except SandboxError as exc:
            if exc.code == "TRANSACTION_NOT_FOUND":
                return None
            raise
        except httpx.HTTPError as exc:
            raise SandboxError(0, "UNREACHABLE", f"The provider could not be reached ({type(exc).__name__}).") from exc

    def messages(self, from_account: str | None = None) -> list[dict]:
        params = {"from_account": from_account} if from_account else None
        return self._request("GET", "/v1/messages", params=params)["messages"]

    def applications(self, applicant_account: str | None = None) -> list[dict]:
        params = {"applicant_account": applicant_account} if applicant_account else None
        return self._request("GET", "/v1/lender/applications", params=params)["applications"]

    def oplog(self, since: int = 0, limit: int = 5000) -> list[dict]:
        return self._request("GET", "/v1/oplog", params={"since": since, "limit": limit})["entries"]

    # ---- side effects (each needs a guard authorization)

    def execute_payment(self, payload: dict, *, authorization: str, idempotency_key: str) -> dict:
        return self._request("POST", "/v1/payments", json=payload,
                             headers=self._authorized(authorization, idempotency_key))

    def send_message(self, payload: dict, *, authorization: str, idempotency_key: str) -> dict:
        return self._request("POST", "/v1/messages", json=payload,
                             headers=self._authorized(authorization, idempotency_key))

    def submit_application(self, payload: dict, *, authorization: str, idempotency_key: str) -> dict:
        return self._request("POST", "/v1/lender/applications", json=payload,
                             headers=self._authorized(authorization, idempotency_key))

    def freeze(self, account_id: str, reason: str, *, authorization: str, idempotency_key: str) -> dict:
        return self._request("POST", f"/v1/accounts/{account_id}/freeze", json={"reason": reason},
                             headers=self._authorized(authorization, idempotency_key))

    def unfreeze(self, account_id: str, *, authorization: str, idempotency_key: str) -> dict:
        return self._request("POST", f"/v1/accounts/{account_id}/unfreeze",
                             headers=self._authorized(authorization, idempotency_key))

    # ---- simulator controls (admin key)

    def reset(self) -> dict:
        return self._request("POST", "/v1/sim/reset", headers=self._admin())

    def seed_accounts(self, accounts: list[dict]) -> dict:
        return self._request("POST", "/v1/sim/accounts", json=accounts, headers=self._admin())

    def import_transactions(self, rows: list[dict]) -> dict:
        return self._request("POST", "/v1/sim/transactions/import", json=rows, headers=self._admin())

    def set_clock(self, now: datetime, *, frozen: bool = False) -> dict:
        return self._request("PUT", "/v1/sim/clock", json={"now": now.isoformat(), "frozen": frozen},
                             headers=self._admin())

    def simulate_incoming(self, to_account: str, from_number: str, amount_paisa: int, *,
                          from_name: str | None = None, txn_type: str = "received",
                          reference: str | None = None) -> dict:
        payload = {"to_account": to_account, "from_number": from_number, "from_name": from_name,
                   "amount_paisa": amount_paisa, "txn_type": txn_type, "reference": reference}
        return self._request("POST", "/v1/sim/incoming", json=payload, headers=self._admin())
