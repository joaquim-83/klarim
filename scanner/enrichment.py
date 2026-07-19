"""Enriquecimento de perfil pós-scan (KL-51 f5) — profiler + IA + CNAE.

Módulo **compartilhado**: o scan worker (`scanner/main.py`) e o `/scan/summary`
(`api/main.py`) chamam a MESMA função, para que **todo** scan — de qualquer caminho —
gere o perfil completo (`site_profile` + `target_classifications`). Antes só o worker
enriquecia, e nem ele gravava os CNAEs (só `enrich_all.py` gravava) — agora ambos o fazem.

Tudo **best-effort**: qualquer erro é só logado, nunca derruba o scan/worker/request.
Imports são lazy para não pesar no boot e evitar ciclos.
"""

from __future__ import annotations

import asyncio
import time

# KL-84 — cache 1h dos setores aprovados (official já estão no prompt base). Evita 1 query
# por scan só para montar o prompt. Fail-open: erro → lista vazia (prompt base).
_APPROVED_SECTORS_CACHE: dict = {"ts": 0.0, "slugs": []}


async def _approved_sectors(store) -> list:
    now = time.time()
    if now - _APPROVED_SECTORS_CACHE["ts"] < 3600:
        return _APPROVED_SECTORS_CACHE["slugs"]
    try:
        rows = await store.list_sectors(["approved"])
        slugs = [r["slug"] for r in rows]
    except Exception:  # noqa: BLE001
        slugs = _APPROVED_SECTORS_CACHE["slugs"]  # mantém o anterior em erro
    _APPROVED_SECTORS_CACHE.update(ts=now, slugs=slugs)
    return slugs


async def enrich_profile(store, target_id: int, url: str, security_score=None,
                         capture_raw: bool = False):
    """Extrai o perfil comercial (crawl multi-page + parsers, KL-50), roda a IA
    (setor + CNAE + descrição + tags, KL-47A/55) e grava `site_profile` +
    `target_classifications`. Best-effort.

    KL-77 (Fase 2): quando ``capture_raw=True`` (só o scan worker pede), devolve o
    **response bruto** já buscado aqui (headers, html, dns, ssl, status, tempo) para
    o worker arquivar no GCS — sem request extra à homepage. O caminho público/anônimo
    (API) passa ``capture_raw=False`` e recebe ``None``: nada muda no fluxo público
    (nem a leitura TLS). Sem captura → retorna ``None``.
    """
    import time as _time
    response_data = None
    try:
        from scanner import profiler
        from scanner.checks import dns_util
        from scanner.checks.base import fetch, base_url, registrable_domain, domain_of

        headers, homepage_html, hp_status, response_time_ms = {}, None, None, None
        try:
            _t0 = _time.monotonic()
            hp = await fetch(base_url(url) + "/", method="GET", follow_redirects=True)
            response_time_ms = int((_time.monotonic() - _t0) * 1000)
            headers = dict(hp.headers)
            hp_status = hp.status_code
            homepage_html = hp.text if hp.status_code == 200 else None
            # Loga o bloqueio explicitamente: um WAF/anti-bot que devolve 403/401/429 ao
            # User-Agent honesto do Klarim (§4.3 — não nos passamos por navegador) faz o
            # crawl vir vazio → perfil esparso. Sem este log, a falha era silenciosa.
            if hp.status_code != 200:
                print(f"[profile] {url}: homepage HTTP {hp.status_code} "
                      f"(anti-bot/WAF bloqueia o UA honesto?) — perfil ficará esparso", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[profile] {url}: falha ao buscar homepage ({exc!r}) — perfil esparso", flush=True)
        dom = registrable_domain(domain_of(url))
        mx = await asyncio.to_thread(dns_util.resolve_mx, dom)
        ns = await asyncio.to_thread(dns_util.resolve_ns, dom)
        # KL-75: TXT (SPF + verificações de plataforma google/facebook/…) só quando o
        # worker vai arquivar/enriquecer — evita 1 query DNS extra no caminho público.
        txt = await asyncio.to_thread(dns_util.resolve_txt, dom) if capture_raw else None

        # KL-77 (Fase 2): captura o response bruto p/ arquivamento no GCS — só quando o
        # worker pede (capture_raw). Reusa o fetch da homepage + o DNS já resolvidos; o
        # SSL vem do cache do tls_analyzer (warm logo após o scan; no tier gratuito pode
        # custar 1 handshake passivo — aceitável e ético). Montado ANTES do profiler/IA
        # para sobreviver a falhas nessas etapas.
        if capture_raw:
            ssl_info: dict = {}
            try:
                from scanner import tls_analyzer
                from scanner.checks.base import host_port
                host, port = host_port(url)
                ssl_info = await tls_analyzer.get_tls_info(host, port)
            except Exception as exc:  # noqa: BLE001 - SSL é best-effort no arquivo
                print(f"[archive] {url}: snapshot TLS indisponível ({exc!r})", flush=True)
            response_data = {
                "http_status": hp_status,
                "response_time_ms": response_time_ms,
                "headers": headers,
                "html": homepage_html or "",
                "dns": {"mx": mx or [], "ns": ns or [], "txt": txt or []},
                "ssl": ssl_info,
            }

        profile = await profiler.build_profile(
            url, homepage_html=homepage_html, headers=headers,
            mx_records=mx, ns_records=ns, security_score=security_score)

        # IA (só se OPENAI_API_KEY): complementa o regex + grava CNAEs.
        await _ai_enrich(store, target_id, dom, homepage_html, profile)

        await store.upsert_site_profile(target_id, profile)
        found = [k for k in ("commercial_email", "phone", "whatsapp", "cnpj", "instagram")
                 if profile.get(k)]
        n_pages = len(profile.get("extraction_sources") or [])
        print(f"[profile] {url} -> maturity {profile.get('maturity_score')} "
              f"páginas={n_pages} homepage={hp_status} "
              f"({', '.join(found) or 'sem sinais'})", flush=True)
    except Exception as exc:  # noqa: BLE001 - enriquecimento nunca derruba nada
        print(f"[profile] falha em {url}: {exc!r}", flush=True)
    # KL-77: devolve o response bruto capturado (ou None) para o worker arquivar.
    return response_data


async def _ai_enrich(store, target_id: int, domain: str, homepage_html, profile: dict) -> None:
    """IA (GPT-4o mini): refina o setor (só alvo fraco) + grava os CNAEs. Best-effort."""
    try:
        from scanner.ai_enrichment import AI_ENRICHMENT_ENABLED, ai_enrich, merge_ai_into_profile
        from discovery.classifier import PRICE_TIERS
    except Exception:  # noqa: BLE001
        return
    if not AI_ENRICHMENT_ENABLED or not homepage_html:
        return
    try:
        known = await _approved_sectors(store)
        ai = await ai_enrich(domain, homepage_html, current_profile=profile, known_sectors=known)
        if not ai:
            return
        changed = merge_ai_into_profile(profile, ai)   # só campos vazios
        sector = ai.get("sector")
        conf = float(ai.get("sector_confidence") or 0.0)
        if sector and conf > 0.7:
            # KL-84 — taxonomia aberta: resolve sinônimo/merge, cria proposto se novo, e devolve
            # o slug canônico (ou 'outro'). Best-effort — nunca derruba o enrich.
            try:
                from discovery.sector_classification import process_classification
                decision = await process_classification(store, ai)
                sector = decision["sector"]
            except Exception as exc:  # noqa: BLE001
                print(f"[ai] process_classification falhou {domain}: {exc!r}", flush=True)
            if sector and sector != "outro":
                tier = PRICE_TIERS.get(sector, "standard")
                # revê regex; preserva manual/ai (KL-54)
                await store.ai_update_classification(target_id, sector, tier, conf)
        # KL-51 f5: grava os CNAEs da IA (source='ai'; a Receita nunca é sobrescrita — KL-55).
        cnaes = ai.get("cnaes") or []
        if cnaes:
            try:
                await store.upsert_target_classifications(target_id, [
                    {"cnae_code": c.get("code"), "cnae_description": c.get("description"),
                     "cnae_section": c.get("section"), "cnae_division": c.get("division"),
                     "confidence": c.get("confidence", 0.0), "source": "ai", "rank": i + 1}
                    for i, c in enumerate(cnaes)])
            except Exception as exc:  # noqa: BLE001
                print(f"[ai] cnae erro {domain}: {exc!r}", flush=True)
        print(f"[ai] {domain}: sector={sector} conf={conf:.2f} cnaes={len(cnaes)} "
              f"preenchidos={changed or 'nenhum'}", flush=True)
    except Exception as exc:  # noqa: BLE001 - IA nunca derruba nada
        print(f"[ai] falha em {domain}: {exc!r}", flush=True)
