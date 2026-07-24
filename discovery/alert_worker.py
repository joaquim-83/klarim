"""Alert Worker — dispara o alerta cold gratuito por e-mail para alvos escaneados.

Elegibilidade: status='scanned', com FALHAS, com e-mail, sem alerta nos últimos
30 dias, não 'unsubscribed'.

Envio COLD (KL-91): cada ciclo escolhe, por e-mail, um dos remetentes rotacionados
(`notifier.cold_alert.pick_sender` — round-robin pelo de menor volume no dia; ver
`docs/ARCHITECTURE.md`), renderiza uma das 3 variantes de **texto puro sem links** e
envia INDIVIDUALMENTE (não mais em batch) via `KlarimMailer.send_cold_alert`, com
**cooldown 30-60s** entre e-mails. Tetos: **limite diário POR remetente**
(`ALERT_SENDER_DAILY_LIMIT`, warmup), o `ALERT_DAILY_LIMIT` global e a **cota mensal**
(`ALERT_MONTHLY_LIMIT`, compartilhada com a evolução do Re-scan). Circuit breaker por
remetente pausa quem passar de `ALERT_SENDER_MAX_BOUNCE_RATE` (amostra ≥20).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from notifier import KlarimMailer, KlarimMailerError, site_name
from notifier import cold_alert
from .store import get_target_store
from .heartbeat import publish_heartbeat
from .contact import email_mx_status, _clean_email
from .alert_scoring import FREE_EMAIL_DOMAINS, calculate_alert_score
from . import worker_control

# Formato de e-mail aceito no batch. 1 e-mail malformado faz o Resend Batch API
# rejeitar os 50 inteiros (422) — por isso validamos antes de montar o batch.
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

_SEV_MAP = {"CRITICA": "critica", "ALTA": "alta", "MEDIA": "media", "BAIXA": "baixa"}

_BONUS_TOKEN_TTL = 30 * 86400  # 30 dias — o e-mail de score 100 pode ser clicado depois


def _is_score100(score, semaphore) -> bool:
    return score == 100 and (str(semaphore or "").lower() == "verde")


def _mask_email(email: str) -> str:
    """Mascara o e-mail para log (privacidade): 'contato@x.com.br' → 'co***o@x.com.br'."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return "(sem e-mail)"
    local, _, domain = email.partition("@")
    if len(local) <= 2:
        masked = local[:1] + "*"
    else:
        masked = local[0] + "***" + local[-1]
    return f"{masked}@{domain}"


def bonus_scan_token(email: str, url: str) -> str:
    """Token de bônus de score 100 (KL-31). Formato IDÊNTICO ao
    api.main._make_scan_token(full=False, bonus=True) — o /scan/summary o verifica
    (o scan completo só roda se o crédito no banco existir; o token só identifica
    o e-mail/URL). Válido por 30 dias."""
    secret = os.environ.get("JWT_SECRET", "") or os.environ.get("UNSUBSCRIBE_SECRET", "")
    payload = {"email": email, "url": url, "full": False, "bonus": True,
               "exp": int(time.time()) + _BONUS_TOKEN_TTL}
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{raw}.{sig}"


def is_demo_target(email: Optional[str] = None, url: Optional[str] = None) -> bool:
    """Alvo de teste (Fix pós-KL-27): não recebe alerta real. Casa por DEMO_EMAIL
    e/ou DEMO_URL (ambos vazios = sem modo demo)."""
    de = os.environ.get("DEMO_EMAIL", "").strip().lower()
    du = os.environ.get("DEMO_URL", "").strip().lower()
    if email and de and email.strip().lower() == de:
        return True
    if url and du and url.strip().lower().startswith(du):
        return True
    return False


def alerts_stopped() -> bool:
    """Kill-switch operacional de envio proativo (alertas + evolução).

    Se o arquivo em ``ALERTS_STOP_FILE`` existir, o ciclo de envio é pulado. No
    compose o flag do operador (`/opt/klarim/STOP_ALERTS`) é montado read-only no
    container; como o bind mount é ao vivo, `touch`/`rm` no host valem já no próximo
    ciclo (≤30min), sem redeploy. Sem a var configurada, nunca pausa (default).
    """
    path = os.environ.get("ALERTS_STOP_FILE", "").strip()
    if not path:
        return False
    try:
        return os.path.exists(path)
    except OSError:  # noqa: BLE001 - na dúvida, não pausa (fail-open)
        return False


def severity_counts_from_checks(checks_json: Optional[dict]) -> Dict[str, int]:
    counts = {"critica": 0, "alta": 0, "media": 0, "baixa": 0}
    for r in (checks_json or {}).get("results", []):
        if r.get("status") == "FAIL":
            key = _SEV_MAP.get(r.get("severity"))
            if key:
                counts[key] += 1
    return counts


async def build_alert_payload(store, target: Dict[str, Any]) -> Dict[str, Any]:
    """Monta o dict de alerta cold (KL-91) a partir de um alvo elegível.

    Reusa os campos já trazidos pelo JOIN de `get_eligible_targets_for_alert`
    (``last_scan_score``/``scan_semaphore``/``scan_fail_count``); cai para `get_scan`
    se faltar. Acrescenta ``sector_label`` + ``sector_avg`` (média do setor) para a
    variante 2 do template — best-effort, nunca derruba o envio. **Não** carrega mais
    risco/benchmark/link (os templates cold são texto puro, sem CTA — ver KL-91)."""
    email = target.get("contact_email")
    if not email:
        raise ValueError("alvo sem e-mail")

    semaphore = target.get("scan_semaphore")
    fail_count = target.get("scan_fail_count")
    score = target.get("last_scan_score")
    if score is None:
        scan = await store.get_scan(target["last_scan_id"]) if target.get("last_scan_id") else None
        if scan is None:
            raise ValueError("alvo sem scan")
        semaphore = semaphore or scan.get("semaphore")
        fail_count = scan.get("fail_count") if fail_count is None else fail_count
        score = scan.get("score") if score is None else score

    # KL-91 — setor + média para a variante 2 (contextual). Só sites com setor conhecido
    # e amostra suficiente (>=10) recebem a média; senão a variante 2 é descartada.
    sector = (target.get("sector") or "").strip().lower()
    sector_label, sector_avg = "", None
    if sector and sector != "outro":
        try:
            from discovery.sector_taxonomy import get_label
            sector_label = get_label(sector)
            bench = await store.sector_benchmark(sector, min_count=10)
            if bench:
                sector_avg = bench.get("avg_score")
        except Exception as exc:  # noqa: BLE001 - setor/benchmark nunca derruba o alerta
            print(f"[alert] setor/benchmark falhou t={target.get('id')}: {exc!r}", flush=True)
    return {
        "target_id": target["id"], "to_email": email, "target_url": target["url"],
        "score": score or 0, "semaphore": semaphore or "", "fail_count": fail_count or 0,
        "sector": sector, "sector_label": sector_label, "sector_avg": sector_avg,
    }


async def send_alert_for_target(store, mailer: KlarimMailer, target: Dict[str, Any]) -> Optional[str]:
    """Envia o alerta cold de UM alvo (envio único), marca 'alerted' e registra no log.

    Usado pelos disparos manuais da API (`/targets/{id}/alert`, `/admin/resend-alert`).
    KL-91: mesmo formato do ciclo automático — texto puro, sem links, remetente cold. O
    disparo manual usa o 1º remetente configurado (a rotação real é do ciclo em lote)."""
    email = target.get("contact_email")
    if not email:
        raise ValueError("alvo sem e-mail")
    payload = await build_alert_payload(store, target)
    score, semaphore, fail_count = payload["score"], payload["semaphore"], payload["fail_count"]

    senders = cold_alert.load_senders()
    if not senders:
        raise KlarimMailerError("nenhum remetente cold configurado (ALERT_SENDER_EMAILS)")
    sender = senders[0]
    variant = cold_alert.choose_variant(payload.get("sector_avg") is not None)
    domain = site_name(target["url"])
    subject, text = cold_alert.build_cold_email(
        variant, domain=domain, score=score,
        sector_label=payload.get("sector_label") or "", sector_avg=payload.get("sector_avg"))

    res = await mailer.send_cold_alert(
        to_email=email, from_address=sender.from_address, subject=subject, text=text,
        template_variant=variant, target_id=target["id"], domain=domain)
    email_id = res.get("email_id")
    # KL-31: score 100 verde → concede o crédito de scan completo grátis (preservado).
    if _is_score100(score, semaphore):
        await store.grant_full_scan_credit(email, target["url"])
    await store.mark_target_alerted(target["id"])
    await store.log_alert(target["id"], email, score, semaphore, fail_count, email_id)
    return email_id


class AlertWorker:
    def __init__(self) -> None:
        # Batch sending (KL-23 / Resend Pro).
        self.batch_size = int(os.environ.get("ALERT_BATCH_SIZE", "50"))
        self.batches_per_cycle = int(os.environ.get("ALERT_BATCHES_PER_CYCLE", "4"))
        self.batch_pause = float(os.environ.get("ALERT_BATCH_PAUSE", "10"))
        self.monthly_limit = int(os.environ.get("ALERT_MONTHLY_LIMIT", "45000"))
        # Saúde de bounce (KL-24): pausa automática se a taxa passar do limite.
        self.max_bounce_rate = float(os.environ.get("ALERT_MAX_BOUNCE_RATE", "8.0"))
        self.bounce_min_sample = int(os.environ.get("ALERT_BOUNCE_MIN_SAMPLE", "20"))
        self.validate_mx = os.environ.get("ALERT_VALIDATE_MX", "true").lower() != "false"
        # Intervalo em minutos tem precedência; ALERT_INTERVAL_HOURS é o fallback.
        interval_minutes = int(os.environ.get("ALERT_INTERVAL_MINUTES", "0"))
        if not interval_minutes:
            interval_minutes = int(os.environ.get("ALERT_INTERVAL_HOURS", "1")) * 60
        self.interval_minutes = interval_minutes or 30
        # KL-85 Parte 1 — lead scoring: filtra alertas abaixo do threshold (conservador: 20).
        self.alert_score_threshold = int(os.environ.get("ALERT_SCORE_THRESHOLD", "20"))
        # KL-91 — cold outreach: rotação de remetentes + envio individual com cooldown.
        # Limite DIÁRIO POR REMETENTE (warmup: começa em 100, sobe manualmente). O
        # cooldown 30-60s entre envios reduz cara de spam; 0/0 em dev/testes (sem espera).
        self.sender_daily_limit = int(os.environ.get("ALERT_SENDER_DAILY_LIMIT", "100"))
        self.send_interval_min = float(os.environ.get("ALERT_SEND_INTERVAL_MIN", "30"))
        self.send_interval_max = float(os.environ.get("ALERT_SEND_INTERVAL_MAX", "60"))
        # Circuit breaker POR REMETENTE (KL-91): pausa quem passar deste bounce rate. Fix 24/07:
        # amostra mínima própria (default 100, não a 20 do safety net global) + janela de 7 dias
        # (ver run_cycle) — um remetente em warmup não é pausado por 3-4 bounces aleatórios cedo.
        self.sender_max_bounce_rate = float(os.environ.get("ALERT_SENDER_MAX_BOUNCE_RATE", "5.0"))
        self.sender_bounce_min_sample = int(os.environ.get(
            "ALERT_SENDER_BOUNCE_MIN_SAMPLE", str(cold_alert.DEFAULT_BOUNCE_MIN_SAMPLE)))
        self.store = get_target_store()
        self._redis = None
        self._last_cycle_at = None
        self._next_cycle_at = None
        self._last_cycle_stats: dict = {}

    async def _reload_settings(self) -> None:
        """Relê os parâmetros editáveis (admin_settings > .env) a cada ciclo (KL-44) —
        permite ajustar no painel sem redeploy. Fail-open: erro mantém os atuais."""
        try:
            g = self.store.get_setting
            self.batch_size = int(await g("ALERT_BATCH_SIZE", self.batch_size))
            self.batches_per_cycle = int(await g("ALERT_BATCHES_PER_CYCLE", self.batches_per_cycle))
            self.batch_pause = float(await g("ALERT_BATCH_PAUSE", self.batch_pause))
            self.monthly_limit = int(await g("ALERT_MONTHLY_LIMIT", self.monthly_limit))
            im = int(await g("ALERT_INTERVAL_MINUTES", 0))
            if im:
                self.interval_minutes = im
            self.alert_score_threshold = int(
                await g("ALERT_SCORE_THRESHOLD", self.alert_score_threshold))
            # KL-91 — limite diário por remetente (knob do warmup, editável no painel).
            self.sender_daily_limit = int(
                await g("ALERT_SENDER_DAILY_LIMIT", self.sender_daily_limit))
        except Exception as exc:  # noqa: BLE001
            print(f"[alert] reload settings falhou (mantém atual): {exc!r}", flush=True)

    def _hb_payload(self) -> dict:
        return {
            "last_cycle_at": self._last_cycle_at.isoformat() if self._last_cycle_at else None,
            "next_cycle_at": self._next_cycle_at.isoformat() if self._next_cycle_at else None,
            "last_cycle_stats": self._last_cycle_stats,
        }

    async def _heartbeat_loop(self) -> None:
        while True:
            await publish_heartbeat("alert", self._hb_payload())
            await asyncio.sleep(60)

    def _mailer(self) -> Optional[KlarimMailer]:
        key = os.environ.get("RESEND_API_KEY")
        return KlarimMailer(key, os.environ.get("RESEND_FROM") or None) if key else None

    async def _check_bounce_health(self) -> bool:
        """Safety net (KL-24): pausa envios se o bounce rate passar do limite.

        Só pausa com amostra mínima (evita pausar por 1–2 bounces cedo). Retorna
        True se é seguro enviar, False se deve pausar.
        """
        h = await self.store.email_health()
        total, bounced = h.get("total", 0), h.get("bounced", 0)
        if total < self.bounce_min_sample:
            return True
        rate = 100.0 * bounced / total if total else 0.0
        if rate > self.max_bounce_rate:
            print(f"[alert] ⚠️ envios pausados — bounce rate {rate:.2f}% "
                  f"(limite {self.max_bounce_rate}%). Corrigir bounces antes de retomar.",
                  flush=True)
            return False
        return True

    async def _validate_batch(self, targets: list) -> list:
        """Filtra alvos com e-mail inválido antes de montar o batch (KL-24 + fix).

        Ordem: (1) limpa o e-mail (URL-decode + tira espaços/lixo); se mudou,
        conserta no banco (self-healing) e usa o limpo no batch; (2) rejeita
        formato inválido (regex) — evita o 422 que derruba o batch inteiro; (3)
        blocklist; (4) domínio sem MX. Alvos ruins são marcados 'descartado'.
        """
        clean = []
        for t in targets:
            raw = (t.get("contact_email") or "").strip()
            # Alvos demo nunca recebem alerta real (Fix pós-KL-27).
            if is_demo_target(email=raw, url=t.get("url")):
                continue
            email = _clean_email(raw)
            if not email or not _EMAIL_RE.match(email):
                await self.store.update_status(t["id"], "descartado")
                print(f"[alert] descartado — e-mail inválido {raw!r} (alvo {t['id']})", flush=True)
                continue
            if email != raw:
                # tinha lixo (ex.: %20contato@…) — conserta no banco e usa o limpo.
                await self.store.update_target_email(t["id"], email)
                t["contact_email"] = email
                print(f"[alert] e-mail limpo: {raw!r} -> {email} (alvo {t['id']})", flush=True)
            if await self.store.is_email_blocked(email):
                await self.store.update_status(t["id"], "descartado")
                print(f"[alert] descartado {email} — na blocklist", flush=True)
                continue
            if self.validate_mx and await asyncio.to_thread(email_mx_status, email) == "no_mx":
                await self.store.discard_target_by_email(email, reason="no_mx")
                print(f"[alert] descartado {email} — domínio sem MX", flush=True)
                continue
            clean.append(t)
        return clean

    async def _redis_client(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(
                    os.environ.get("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)
            except Exception:  # noqa: BLE001 - sem Redis o bounce cai no banco a cada ciclo
                self._redis = False
        return self._redis or None

    async def _domain_bounced(self, domain: str, cache: dict) -> bool:
        """Domínio com bounce anterior? Cache em memória (por ciclo) + Redis (24h). Fail-open:
        qualquer erro → False (não penaliza por falha de infra)."""
        dom = (domain or "").strip().lower()
        if not dom:
            return False
        # Provedores genéricos (gmail/outlook/…) NUNCA são penalizados por domínio: um bounce em
        # joao@gmail.com não diz nada sobre maria@gmail.com. Evita a query + polui cache (fix 2026-07-20).
        if dom in FREE_EMAIL_DOMAINS:
            return False
        if dom in cache:
            return cache[dom]
        r = await self._redis_client()
        key = f"bounce_domain:{dom}"
        if r is not None:
            try:
                v = await r.get(key)
                if v is not None:
                    cache[dom] = v == "1"
                    return cache[dom]
            except Exception:  # noqa: BLE001
                r = None
        try:
            bounced = await self.store.domain_has_bounce(dom)
        except Exception:  # noqa: BLE001 - na dúvida, não penaliza
            bounced = False
        cache[dom] = bounced
        if r is not None:
            try:
                await r.set(key, "1" if bounced else "0", ex=86400)
            except Exception:  # noqa: BLE001
                pass
        return bounced

    async def _apply_alert_scoring(self, targets: list) -> tuple:
        """KL-85 — grava o `alert_quality_score` de TODOS os alvos e devolve só os que passam do
        threshold. Fail-safe: se o scoring de um alvo estourar, o alvo é MANTIDO (nunca perde um
        lead bom por bug de scoring). Retorna (kept, skipped_low_quality, avg_score_dos_kept).

        Fix 2026-07-23: LOG DETALHADO e PERMANENTE de cada lead pulado por baixa qualidade
        (score < threshold) — id, e-mail mascarado, score e os sinais que o compuseram. Sem
        isto era impossível diagnosticar "1375 elegíveis → 0 enviados" (era o lead scoring,
        não daily-limit/bounce/blocklist). `SAMPLE`: loga no máx. os primeiros N skips do ciclo
        (evita flood) + o total no resumo do ciclo."""
        kept, kept_scores, skipped = [], [], 0
        bounce_cache: dict = {}
        skip_log_budget = int(os.environ.get("ALERT_SKIP_LOG_SAMPLE", "20"))
        for t in targets:
            try:
                email = (t.get("contact_email") or "").strip().lower()
                edomain = email.rsplit("@", 1)[1] if "@" in email else ""
                bounced = await self._domain_bounced(edomain, bounce_cache) if edomain else False
                result = calculate_alert_score(t, email, bounced)
                score = result["score"]
                t["_alert_score"] = score
                try:
                    await self.store.update_target_alert_score(t["id"], score)
                except Exception as exc:  # noqa: BLE001 - gravar nunca bloqueia o envio
                    print(f"[alert] falha ao gravar score (alvo {t.get('id')}): {exc!r}", flush=True)
                if score < self.alert_score_threshold:
                    skipped += 1
                    if skip_log_budget > 0:
                        skip_log_budget -= 1
                        sig = " ".join(f"{s['signal']}={s['points']:+d}"
                                       for s in result.get("signals", [])) or "sem-sinais"
                        print(f"[alert] skip lead t={t.get('id')} {_mask_email(email)} "
                              f"score={score}<{self.alert_score_threshold} [{sig}]", flush=True)
                    continue
            except Exception as exc:  # noqa: BLE001 - bug de scoring NÃO derruba o alvo
                print(f"[alert] scoring falhou (alvo {t.get('id')}), mantendo: {exc!r}", flush=True)
            kept.append(t)
            kept_scores.append(t.get("_alert_score", 0))
        avg = round(sum(kept_scores) / len(kept_scores), 1) if kept_scores else 0
        if skipped:
            print(f"[alert] lead scoring: {len(kept)} aprovados, {skipped} pulados "
                  f"(< threshold {self.alert_score_threshold}); média dos aprovados {avg}", flush=True)
        return kept, skipped, avg

    async def _send_cooldown(self) -> None:
        """KL-91 — intervalo randômico ENTRE envios individuais (30-60s por padrão),
        para reduzir a cara de spam. 0/0 (dev/testes) → sem espera."""
        lo, hi = self.send_interval_min, self.send_interval_max
        if hi <= 0:
            return
        await asyncio.sleep(random.uniform(lo, hi) if hi > lo else lo)

    async def run_cycle(self) -> dict:
        stats = {"eligible": 0, "sent": 0, "failed": 0,
                 "errors": 0, "skipped": 0, "invalid": 0}
        await self._reload_settings()  # KL-44: config ao vivo (admin_settings > .env)
        mailer = self._mailer()
        if mailer is None:
            print("[alert] RESEND_API_KEY não configurada; ciclo pulado", flush=True)
            return stats

        # Controle centralizado (KL-32): pausa por MCP/painel. Aditivo ao STOP_ALERTS.
        if not worker_control.is_enabled("alert"):
            print("[alert] worker pausado (worker_control); pulando ciclo", flush=True)
            stats["disabled"] = True
            return stats

        # Kill-switch operacional (STOP_ALERTS, KL-27): pausa manual sem redeploy.
        if alerts_stopped():
            print("[alert] STOP_ALERTS ativo; ciclo pulado", flush=True)
            stats["paused_by_flag"] = True
            return stats

        # Safety net de bounce (KL-24) — pausa se a taxa estiver crítica.
        if not await self._check_bounce_health():
            stats["paused"] = True
            return stats

        # Cota mensal GLOBAL (alertas + evolução). Único teto no plano Pro.
        sent_month = await self.store.count_proactive_emails_this_month()
        if sent_month >= self.monthly_limit:
            print(f"[alert] cota mensal atingida ({sent_month}/{self.monthly_limit}); "
                  f"ciclo pulado", flush=True)
            return stats

        # Warmup do domínio novo (klarimscan.com): LIMITE DIÁRIO de alertas proativos,
        # ajustável ao vivo pelo painel (ALERT_DAILY_LIMIT). Default alto = sem limite.
        daily_limit = int(await self.store.get_setting("ALERT_DAILY_LIMIT", "5000"))
        sent_today = await self.store.count_alerts_sent_today()
        if sent_today >= daily_limit:
            print(f"[alert] limite diário atingido ({sent_today}/{daily_limit}); "
                  f"ciclo pulado", flush=True)
            stats["daily_limit_reached"] = True
            return stats

        # KL-91 — remetentes cold (rotação) + circuit breaker por bounce POR remetente.
        senders = cold_alert.load_senders()
        if not senders:
            print("[alert] nenhum remetente cold configurado (ALERT_SENDER_EMAILS); "
                  "ciclo pulado", flush=True)
            stats["no_senders"] = True
            return stats
        try:
            # Fix 24/07: janela de 7 dias — o circuit breaker julga o bounce rate RECENTE
            # (bounces antigos saem do cálculo; um remetente se recupera após corrigir a lista).
            by_domain = await self.store.email_health_by_domain(days=7)
        except Exception as exc:  # noqa: BLE001 - fail-open: sem stats, ninguém é pausado
            by_domain = {}
            print(f"[alert] email_health_by_domain falhou (segue): {exc!r}", flush=True)
        paused = cold_alert.flag_high_bounce(senders, by_domain, self.sender_max_bounce_rate,
                                             self.sender_bounce_min_sample)
        for dom, rate in paused:
            print(f"[alert] CRITICAL: remetente {dom} pausado — bounce rate {rate}% "
                  f"(> {self.sender_max_bounce_rate}%). A rotação segue nos demais.", flush=True)
        stats["senders_paused"] = [d for d, _ in paused]

        # Contagem por remetente HOJE (base da rotação + limite diário por remetente).
        try:
            sent_by_domain = await self.store.count_alerts_sent_today_by_domain()
        except Exception as exc:  # noqa: BLE001 - fail-open
            sent_by_domain = {}
            print(f"[alert] count_alerts_sent_today_by_domain falhou (segue): {exc!r}", flush=True)
        counts = {s.from_domain: int(sent_by_domain.get(s.from_domain, 0)) for s in senders}
        sender_room = sum(max(0, self.sender_daily_limit - counts[s.from_domain])
                          for s in senders if s.status == "active")
        if sender_room <= 0:
            print(f"[alert] todos os remetentes atingiram o limite diário "
                  f"({self.sender_daily_limit}/remetente) ou estão pausados; ciclo pulado",
                  flush=True)
            stats["sender_limit_reached"] = True
            return stats

        # Throttle dinâmico (KL-32): batch_size + max_per_hour lidos do controle.
        cfg = worker_control.worker_config("alert")
        batch_size = int(cfg.get("batch_size") or self.batch_size)
        self.batch_size = batch_size
        # SEND cap: quantos e-mails ENVIAR no ciclo — throttle por hora + o que cabe no
        # intervalo dado o cooldown (evita ciclos que estouram o próprio período) + cotas.
        send_cap = batch_size * self.batches_per_cycle
        max_per_hour = cfg.get("max_per_hour")
        if max_per_hour:
            per_cycle = max(1, int(int(max_per_hour) * self.interval_minutes / 60))
            send_cap = min(send_cap, per_cycle)
        avg_cd = (self.send_interval_min + self.send_interval_max) / 2.0
        if avg_cd > 0:  # com cooldown, cabe ~ (80% do intervalo) / cooldown médio
            send_cap = min(send_cap, max(1, int(self.interval_minutes * 60 * 0.8 / avg_cd)))
        send_cap = min(send_cap, self.monthly_limit - sent_month,
                       daily_limit - sent_today, sender_room)
        # FETCH cap: quantos CANDIDATOS avaliar — MUITO maior que o send_cap. O lead scoring
        # (KL-85) corta a maioria; buscar só `send_cap` fazia o worker reler os mesmos alvos
        # de baixa qualidade da frente e mandar 0 (livelock, fix 2026-07-23). A query já ordena
        # os leads de maior qualidade (e-mail no domínio do site) primeiro.
        fetch_cap = int(await self.store.get_setting("ALERT_FETCH_CAP", "200"))
        want = min(max(fetch_cap, send_cap), self.monthly_limit - sent_month)
        raw_targets = await self.store.get_eligible_targets_for_alert(limit=max(0, want))
        stats["fetched"] = len(raw_targets)
        stats["eligible"] = len(raw_targets)   # compat (contagem avaliada neste ciclo)

        # Validação pré-envio (KL-24): remove blocklist + domínios sem MX.
        targets = await self._validate_batch(raw_targets)
        stats["invalid"] = len(raw_targets) - len(targets)

        # KL-85 Parte 1 — lead scoring: grava o score de TODOS e filtra abaixo do threshold.
        targets, skipped_low, avg_score = await self._apply_alert_scoring(targets)
        stats["skipped_low_quality"] = skipped_low
        stats["avg_alert_score"] = avg_score
        # Melhores leads primeiro: dado o send_cap, envia os de MAIOR score (mais provável clique).
        targets.sort(key=lambda t: t.get("_alert_score", 0), reverse=True)

        # Envio INDIVIDUAL com rotação + cooldown (KL-91). Sem batch: cada e-mail escolhe o
        # remetente de menor volume hoje, renderiza a variante e há intervalo entre envios.
        stats["variants"] = {1: 0, 2: 0, 3: 0}
        deadline = time.monotonic() + self.interval_minutes * 60 * 0.8 if avg_cd > 0 else None
        for idx, t in enumerate(targets):
            if stats["sent"] >= send_cap:   # atingiu o teto de ENVIO do ciclo (resto p/ o próximo)
                stats["skipped"] += len(targets) - idx
                break
            # Respeita cota mensal + limite diário global em tempo real (já enviado no ciclo).
            room = min(self.monthly_limit - (sent_month + stats["sent"]),
                       daily_limit - (sent_today + stats["sent"]))
            if room <= 0:
                stats["skipped"] += len(targets) - idx
                break
            sender = cold_alert.pick_sender(senders, counts, self.sender_daily_limit)
            if sender is None:  # todos os remetentes bateram o limite diário no ciclo
                stats["skipped"] += len(targets) - idx
                print("[alert] remetentes esgotados no ciclo; parando", flush=True)
                break
            try:
                payload = await build_alert_payload(self.store, t)
            except Exception as exc:  # noqa: BLE001 - alvo ruim é pulado sem derrubar o ciclo
                stats["errors"] += 1
                print(f"[alert] pulando {t.get('url')}: {exc!r}", flush=True)
                continue

            domain = site_name(payload["target_url"])
            variant = cold_alert.choose_variant(payload.get("sector_avg") is not None)
            subject, text = cold_alert.build_cold_email(
                variant, domain=domain, score=payload["score"],
                sector_label=payload.get("sector_label") or "",
                sector_avg=payload.get("sector_avg"))
            try:
                res = await mailer.send_cold_alert(
                    to_email=payload["to_email"], from_address=sender.from_address,
                    subject=subject, text=text, template_variant=variant,
                    target_id=payload["target_id"], domain=domain)
            except Exception as exc:  # noqa: BLE001 - um envio ruim não derruba o ciclo
                msg = str(exc).lower()
                if isinstance(exc, KlarimMailerError) and ("422" in msg or "invalid" in msg):
                    # E-mail ruim (rejeitado pelo Resend) → descarta o alvo e SEGUE.
                    await self.store.log_alert(payload["target_id"], payload["to_email"],
                                               payload.get("score"), payload.get("semaphore"),
                                               payload.get("fail_count"), None, status="failed")
                    await self.store.update_status(payload["target_id"], "descartado")
                    stats["invalid"] += 1
                    stats["failed"] += 1
                    print(f"[alert] descartado — Resend rejeitou {payload['to_email']!r}",
                          flush=True)
                    continue
                # Infra/inesperado (5xx/rede): loga a falha, NÃO descarta, e ABORTA o ciclo.
                stats["errors"] += 1
                await self.store.log_alert(payload["target_id"], payload["to_email"],
                                           payload.get("score"), payload.get("semaphore"),
                                           payload.get("fail_count"), None, status="failed")
                print(f"[alert] erro ao enviar (aborta ciclo): {exc!r}", flush=True)
                break
            if res.get("blocked"):  # blocklist (defensivo; já validado antes)
                continue

            email_id = res.get("email_id")
            counts[sender.from_domain] += 1
            await self.store.mark_target_alerted(payload["target_id"])
            await self.store.log_alert(payload["target_id"], payload["to_email"],
                                       payload.get("score"), payload.get("semaphore"),
                                       payload.get("fail_count"), email_id)
            # KL-31: convite de score 100 → concede o crédito de scan completo grátis.
            if _is_score100(payload.get("score"), payload.get("semaphore")):
                await self.store.grant_full_scan_credit(payload["to_email"], payload["target_url"])
            stats["sent"] += 1
            stats["variants"][variant] += 1

            # Cooldown randômico ENTRE envios (não após o último) — respeita o deadline
            # do ciclo (o restante entra no próximo ciclo).
            if idx + 1 < len(targets):
                if deadline is not None and time.monotonic() + self.send_interval_max > deadline:
                    stats["skipped"] += len(targets) - (idx + 1)
                    print("[alert] deadline do ciclo atingido; restante fica p/ o próximo ciclo",
                          flush=True)
                    break
                await self._send_cooldown()

        remaining = await self.store.count_eligible_targets_for_alert()
        print(f"[alert] ciclo: {stats['sent']} enviados "
              f"(variantes {stats['variants']}), {remaining} restantes | "
              f"avaliados={stats.get('fetched', 0)} inválidos={stats['invalid']} "
              f"baixa_qualidade={stats.get('skipped_low_quality', 0)} "
              f"adiados={stats['skipped']} erros={stats['errors']} | "
              f"mês {sent_month + stats['sent']}/{self.monthly_limit}", flush=True)
        return stats

    async def start(self) -> None:
        senders = ", ".join(s.from_domain for s in cold_alert.load_senders()) or "(nenhum)"
        print(f"[alert] iniciado (KL-91 rotação cold: {senders}; "
              f"{self.sender_daily_limit}/remetente/dia, cooldown "
              f"{int(self.send_interval_min)}-{int(self.send_interval_max)}s, "
              f"intervalo {self.interval_minutes}min, limite {self.monthly_limit // 1000}k/mês)",
              flush=True)
        asyncio.create_task(self._heartbeat_loop())
        while True:
            try:
                self._last_cycle_stats = await self.run_cycle()
            except Exception as exc:  # noqa: BLE001
                print(f"[alert] ciclo falhou: {exc!r}", flush=True)
            self._last_cycle_at = datetime.now(timezone.utc)
            self._next_cycle_at = self._last_cycle_at + timedelta(minutes=self.interval_minutes)
            await publish_heartbeat("alert", self._hb_payload())
            await asyncio.sleep(self.interval_minutes * 60)
