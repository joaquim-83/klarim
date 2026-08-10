"""KL-150 fix (P2) — o regex `_BOT_UA_RE` (usado no `al_server_metrics` para tirar bots/ferramentas
que escapam do classificador `is_bot` da contagem de VISITANTES) casa bots e NÃO casa navegadores
reais. POSIX `~*` (Postgres) ≈ Python `re.IGNORECASE` para esta alternação simples."""
from __future__ import annotations

import re

from discovery.store import _BOT_UA_RE

_RE = re.compile(_BOT_UA_RE, re.IGNORECASE)

# UAs de bot/ferramenta (incl. as nossas próprias tools) — DEVEM casar (contam como bot, não visitante).
_BOT_UAS = [
    "Klarim Security Gate/1.0",
    "KlarimScanner/1.0 (+https://klarim.net; security monitoring)",
    "Googlebot/2.1 (+http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)",
    "SemrushBot/7~bl",
    "Mozilla/5.0 (compatible; PetalBot;+https://webmaster.petalsearch.com/site/petalbot)",
    "python-requests/2.31.0",
    "curl/8.0.1",
    "Wget/1.21.3",
    "node-fetch/1.0",
    "HeadlessChrome/120.0.0.0",
    "http://klarim.net/wp-admin/install.php?step=1",
    "Scrapy/2.11 (+https://scrapy.org)",
]

# Navegadores REAIS — NÃO devem casar (contam como visitante).
_HUMAN_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0",
]


def test_bot_uas_match():
    for ua in _BOT_UAS:
        assert _RE.search(ua), f"deveria casar como bot: {ua}"


def test_human_uas_do_not_match():
    for ua in _HUMAN_UAS:
        assert not _RE.search(ua), f"NÃO deveria casar (navegador real): {ua}"
