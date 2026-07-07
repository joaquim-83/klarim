"""Modelos e constantes de pagamento."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional


# Faixas de preço por setor (em centavos). MVP usa sempre 'standard' (R$ 29).
PRICING = {
    "basic": 1900,         # comércio local, blogs, portfólios
    "standard": 2900,      # hotéis, pousadas, restaurantes
    "professional": 3900,  # e-commerces, escolas, contabilidade
    "enterprise": 4900,    # clínicas, saúde, jurídico
}

DEFAULT_TIER = "standard"


class PaymentStatus:
    """Status de pagamento da AbacatePay."""

    PENDING = "PENDING"
    PAID = "PAID"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"

    PAID_STATES = {PAID}


def amount_display(cents: int) -> str:
    """Formata centavos como 'R$ 29,00'."""
    return f"R$ {cents // 100},{cents % 100:02d}"


def mask_email(email: str) -> str:
    """Mascara um e-mail para exibição: 'hotel@x.com' -> 'h***l@x.com'."""
    if not email or "@" not in email:
        return "***"
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        masked = (local[0] if local else "") + "***"
    else:
        masked = local[0] + "***" + local[-1]
    return f"{masked}@{domain}"


@dataclass
class RecoveryToken:
    """Token temporário de recuperação de relatórios (TTL 24h)."""

    token: str
    buyer_email: str
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    used_at: Optional[str] = None


@dataclass
class Charge:
    """Uma cobrança PIX criada na AbacatePay + persistida localmente."""

    charge_id: str
    target_url: str
    amount_cents: int
    status: str = PaymentStatus.PENDING
    br_code: Optional[str] = None
    br_code_base64: Optional[str] = None
    expires_at: Optional[str] = None
    created_at: Optional[str] = None
    paid_at: Optional[str] = None
    buyer_email: Optional[str] = None
    report_email_sent: bool = False
    # null | pending | sending | sent | failed
    email_status: Optional[str] = None

    @property
    def is_paid(self) -> bool:
        return self.status in PaymentStatus.PAID_STATES

    def to_public_dict(self) -> dict:
        """Payload seguro para o frontend (não vaza campos internos)."""
        return {
            "charge_id": self.charge_id,
            "amount": self.amount_cents,
            "amount_display": amount_display(self.amount_cents),
            "br_code": self.br_code,
            "qr_code_base64": _as_data_uri(self.br_code_base64),
            "expires_at": self.expires_at,
            "status": self.status,
            "paid": self.is_paid,
        }

    def to_dict(self) -> dict:
        return asdict(self)


def _as_data_uri(b64: Optional[str]) -> Optional[str]:
    """Garante o prefixo data: para a imagem base64 do QR code."""
    if not b64:
        return None
    if b64.startswith("data:"):
        return b64
    return f"data:image/png;base64,{b64}"
