"""Check 09 — Source maps expostos (Severidade: CRÍTICA).

Spec: tenta GET em ``{url}/static/js/*.js.map`` e ``asset-manifest.json``.

Exposed JavaScript source maps (``.js.map``) reveal original, unminified source
— including comments, internal paths and sometimes secrets. This check:

  1. probes ``/asset-manifest.json`` (Create-React-App build artifact);
  2. reads the homepage, extracts the referenced ``.js`` bundles, and tries the
     corresponding ``.map`` for the first few of them.

A hit only counts when the response is 200 *and* the body actually parses as a
source map / manifest — never on an SPA HTML fallback.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

import httpx

from .base import (
    CheckResult,
    Status,
    Severity,
    fetch,
    with_scheme,
    base_url,
    looks_like_html,
)

NAME = "Source maps expostos"

# Extract src="..."/src='...' from <script> tags pointing at .js files.
_SCRIPT_SRC_RE = re.compile(
    r"""<script[^>]+src\s*=\s*['"]([^'"]+?\.js(?:\?[^'"]*)?)['"]""",
    re.IGNORECASE,
)

# How many JS bundles to probe for a sibling .map (each probe is rate-limited).
_MAX_MAP_PROBES = 4


async def check(url: str) -> CheckResult:
    root = base_url(url)
    https_url = with_scheme(url, "https")
    exposed: list[str] = []
    probed: list[str] = []

    # 1) asset-manifest.json (CRA).
    manifest_url = urljoin(root + "/", "asset-manifest.json")
    probed.append(manifest_url)
    try:
        resp = await fetch(manifest_url, method="GET", follow_redirects=True)
        if resp.status_code == 200 and not looks_like_html(resp):
            if _is_json_manifest(resp.text):
                exposed.append(manifest_url)
    except (httpx.HTTPError, OSError):
        pass

    # 2) Read homepage, discover .js bundles, probe their .map siblings.
    js_urls: list[str] = []
    try:
        home = await fetch(https_url, method="GET", follow_redirects=True)
        if home.status_code == 200:
            for m in _SCRIPT_SRC_RE.findall(home.text):
                abs_js = urljoin(str(home.url), m.split("?")[0])
                if abs_js not in js_urls:
                    js_urls.append(abs_js)
    except (httpx.HTTPError, OSError):
        pass

    for js_url in js_urls[:_MAX_MAP_PROBES]:
        map_url = js_url + ".map"
        probed.append(map_url)
        try:
            resp = await fetch(map_url, method="GET", follow_redirects=True)
        except (httpx.HTTPError, OSError):
            continue
        if resp.status_code == 200 and not looks_like_html(resp):
            if _is_sourcemap(resp.text):
                exposed.append(map_url)

    if exposed:
        return CheckResult(
            name=NAME,
            status=Status.FAIL,
            severity=Severity.CRITICA,
            evidence=(
                f"{len(exposed)} artefato(s) de source map/manifest expostos: "
                + ", ".join(exposed)
            ),
            details={"exposed": exposed, "probed": probed},
        )

    return CheckResult(
        name=NAME,
        status=Status.PASS,
        severity=Severity.CRITICA,
        evidence=(
            f"Nenhum source map exposto ({len(probed)} caminho(s) testado(s), "
            f"{len(js_urls)} bundle(s) JS inspecionado(s))."
        ),
        details={"probed": probed, "js_bundles": js_urls},
    )


def _is_sourcemap(text: str) -> bool:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(data, dict) and "mappings" in data and (
        "sources" in data or "version" in data
    )


def _is_json_manifest(text: str) -> bool:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    # CRA manifest has "files"/"entrypoints"; any .map reference is a red flag.
    return "files" in data or "entrypoints" in data or ".map" in text
