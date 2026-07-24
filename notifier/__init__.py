"""Klarim notifier — envio de e-mail (Resend): alertas e entrega de relatórios."""

from __future__ import annotations

from .email_client import (
    KlarimMailer,
    KlarimMailerError,
    EMAIL_TYPES,
    semaphore_from_score,
    site_name,
    unsubscribe_token,
    build_unsubscribe_link,
    batch_idempotency_key,
    verify_resend_signature,
    generate_unsubscribe_token,
    verify_unsubscribe_token,
    build_cold_unsubscribe_headers,
)

__all__ = [
    "KlarimMailer",
    "KlarimMailerError",
    "EMAIL_TYPES",
    "semaphore_from_score",
    "site_name",
    "unsubscribe_token",
    "build_unsubscribe_link",
    "batch_idempotency_key",
    "verify_resend_signature",
    "generate_unsubscribe_token",
    "verify_unsubscribe_token",
    "build_cold_unsubscribe_headers",
]
