"""Klarim discovery — motor de aquisição (CT logs + fingerprint + e-mail)."""

from __future__ import annotations

from .ct_client import CTClient
from .fingerprint import detect_platform, FINGERPRINTS
from .contact import extract_email
from .classifier import classify_sector, SECTOR_KEYWORDS, PRICE_TIERS
from .store import TargetStore, get_target_store
from .worker import DiscoveryWorker

__all__ = [
    "CTClient",
    "detect_platform",
    "FINGERPRINTS",
    "extract_email",
    "classify_sector",
    "SECTOR_KEYWORDS",
    "PRICE_TIERS",
    "TargetStore",
    "get_target_store",
    "DiscoveryWorker",
]
