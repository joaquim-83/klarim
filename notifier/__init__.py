"""Klarim notifier — envio de e-mail (Resend): alertas e entrega de relatórios."""

from __future__ import annotations

from .email_client import (
    KlarimMailer,
    KlarimMailerError,
    semaphore_from_score,
    site_name,
)

__all__ = [
    "KlarimMailer",
    "KlarimMailerError",
    "semaphore_from_score",
    "site_name",
]
