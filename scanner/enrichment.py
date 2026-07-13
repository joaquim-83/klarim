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


async def enrich_profile(store, target_id: int, url: str, security_score=None) -> None:
    """Extrai o perfil comercial (crawl multi-page + parsers, KL-50), roda a IA
    (setor + CNAE + descrição + tags, KL-47A/55) e grava `site_profile` +
    `target_classifications`. Best-effort."""
    try:
        from scanner import profiler
        from scanner.checks import dns_util
        from scanner.checks.base import fetch, base_url, registrable_domain, domain_of

        headers, homepage_html = {}, None
        try:
            hp = await fetch(base_url(url) + "/", method="GET", follow_redirects=True)
            headers = dict(hp.headers)
            homepage_html = hp.text if hp.status_code == 200 else None
        except Exception:  # noqa: BLE001
            pass
        dom = registrable_domain(domain_of(url))
        mx = await asyncio.to_thread(dns_util.resolve_mx, dom)
        ns = await asyncio.to_thread(dns_util.resolve_ns, dom)
        profile = await profiler.build_profile(
            url, homepage_html=homepage_html, headers=headers,
            mx_records=mx, ns_records=ns, security_score=security_score)

        # IA (só se OPENAI_API_KEY): complementa o regex + grava CNAEs.
        await _ai_enrich(store, target_id, dom, homepage_html, profile)

        await store.upsert_site_profile(target_id, profile)
        found = [k for k in ("commercial_email", "phone", "whatsapp", "cnpj", "instagram")
                 if profile.get(k)]
        print(f"[profile] {url} -> maturity {profile.get('maturity_score')} "
              f"({', '.join(found) or 'sem sinais'})", flush=True)
    except Exception as exc:  # noqa: BLE001 - enriquecimento nunca derruba nada
        print(f"[profile] falha em {url}: {exc!r}", flush=True)


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
        ai = await ai_enrich(domain, homepage_html, current_profile=profile)
        if not ai:
            return
        changed = merge_ai_into_profile(profile, ai)   # só campos vazios
        sector = ai.get("sector")
        conf = float(ai.get("sector_confidence") or 0.0)
        if sector and sector != "outro" and conf > 0.7:
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
