"""Client da API AbacatePay (PIX transparente + webhooks).

Usa httpx diretamente (sem SDK). Docs: https://docs.abacatepay.com/llms.txt

Convenções da AbacatePay:
- Base URL v2, auth via `Authorization: Bearer <api_key>`.
- Valores em centavos.
- Respostas no formato ``{"data": {...}, "success": bool, "error": null}``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from typing import Any, Dict, Optional

import httpx

DEFAULT_BASE_URL = "https://api.abacatepay.com/v2"
REQUEST_TIMEOUT = 15.0
MAX_RETRIES = 3


class AbacatePayError(RuntimeError):
    """Erro ao falar com a AbacatePay (após retries, ou resposta de erro)."""


class AbacatePayClient:
    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL) -> None:
        if not api_key:
            raise ValueError("ABACATEPAY_API_KEY não configurada")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Faz a requisição com retry/backoff em erros 5xx e de rede."""
        url = f"{self.base_url}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                    resp = await client.request(
                        method, url, headers=self._headers(), json=json, params=params
                    )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            if resp.status_code >= 500:
                last_exc = AbacatePayError(f"AbacatePay {resp.status_code}: {resp.text[:200]}")
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            try:
                body = resp.json()
            except ValueError:
                raise AbacatePayError(
                    f"Resposta não-JSON da AbacatePay ({resp.status_code}): {resp.text[:200]}"
                )

            if resp.status_code >= 400 or body.get("error"):
                raise AbacatePayError(
                    f"AbacatePay {resp.status_code}: {body.get('error') or body}"
                )
            return body

        raise AbacatePayError(f"AbacatePay indisponível após {MAX_RETRIES} tentativas: {last_exc!r}")

    async def create_pix_charge(self, amount_cents: int, description: str) -> Dict[str, Any]:
        """Cria uma cobrança PIX transparente.

        Retorna o objeto ``data`` da AbacatePay (id, brCode, brCodeBase64, ...).
        """
        payload = {
            "method": "PIX",
            "data": {"amount": amount_cents, "description": description},
        }
        body = await self._request("POST", "/transparents/create", json=payload)
        return body.get("data", {})

    async def check_payment(self, charge_id: str) -> Dict[str, Any]:
        """Verifica o status de uma cobrança. Retorna o objeto ``data``."""
        body = await self._request("GET", "/transparents/check", params={"id": charge_id})
        return body.get("data", {})

    async def create_webhook(
        self, name: str, endpoint: str, secret: str, events: list[str]
    ) -> Dict[str, Any]:
        """Registra um webhook na AbacatePay."""
        payload = {"name": name, "endpoint": endpoint, "secret": secret, "events": events}
        body = await self._request("POST", "/webhooks/create", json=payload)
        return body.get("data", {})

    async def simulate_payment(self, charge_id: str) -> Dict[str, Any]:
        """Simula o pagamento de uma cobrança — **só funciona com chave dev/sandbox**.

        Útil para testar o fluxo completo sem um PIX real.
        """
        body = await self._request(
            "POST", "/transparents/simulate-payment", params={"id": charge_id}
        )
        return body.get("data", {})


def verify_webhook_signature(secret: str, raw_body: bytes, signature: str) -> bool:
    """Valida a assinatura HMAC-SHA256 (header ``X-Webhook-Signature``, base64).

    Compara em tempo constante. Aceita tanto base64 quanto hex por robustez.
    """
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256)
    expected_b64 = base64.b64encode(mac.digest()).decode("ascii")
    expected_hex = mac.hexdigest()
    sig = signature.strip()
    return hmac.compare_digest(sig, expected_b64) or hmac.compare_digest(sig, expected_hex)
