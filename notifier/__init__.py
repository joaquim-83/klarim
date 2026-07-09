"""Klarim notifier — envio de e-mail (Resend): alertas e entrega de relatórios."""

from __future__ import annotations

from .email_client import (
    KlarimMailer,
    KlarimMailerError,
    semaphore_from_score,
    site_name,
    unsubscribe_token,
    build_unsubscribe_link,
    batch_idempotency_key,
    verify_resend_signature,
)

__all__ = [
    "KlarimMailer",
    "KlarimMailerError",
    "semaphore_from_score",
    "site_name",
    "unsubscribe_token",
    "build_unsubscribe_link",
    "batch_idempotency_key",
    "verify_resend_signature",
]
