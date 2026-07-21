"""Scan orchestration.

``run_scan(url)`` executes every registered check (discovered dynamically and
ordered by ``scanner.checks.ALL_CHECKS``) against a single target and returns a
:class:`ScanReport` bundling every :class:`CheckResult` plus the computed score.
The number of checks is dynamic and grows as new ``check_*`` modules are added.

The checks run **in sequence** (spec: "executar os checks em sequência"),
which also keeps the per-domain rate limit trivially satisfied.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

from .checks import ALL_CHECKS, FREE_CHECKS
from .checks.base import CheckResult, Status, Severity, normalize_url

# Concorrência dos checks (KL-51 f3 hotfix): rodar os 48 checks em SEQUÊNCIA levava
# ~80s (site grande) a >180s (site grande + cache frio) → estourava o timeout do proxy
# (504). Rodamos em paralelo com um teto: o rate limiter de `base.fetch` é por-domínio
# (asyncio.Lock), então requests ao MESMO domínio continuam serializados em 1 req/s
# (regra do scanner passivo preservada); só os checks de domínios distintos (crt.sh,
# HIBP, DNS, TLS…) é que passam a se sobrepor. Teto p/ não estourar o event loop /
# thread pool do worker único.
SCAN_MAX_CONCURRENCY = int(os.environ.get("SCAN_MAX_CONCURRENCY", "12"))
from .checks.classifications import classify
from .scoring import ScoreBreakdown, compute_score


@dataclass
class ScanReport:
    url: str
    started_at: str
    finished_at: str
    duration_s: float
    results: List[CheckResult] = field(default_factory=list)
    score: Optional[ScoreBreakdown] = None
    privacy: Optional[dict] = None   # KL-44 P5: indicadores de privacidade (score SEPARADO)
    # KL-94: gate de acessibilidade. `ok` = escaneou; senão o scan foi ABORTADO antes dos checks
    # (sem score falso). Valores: ok | domain_not_found | dns_error | unreachable.
    status: str = "ok"
    error_detail: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_s": round(self.duration_s, 2),
            "score": self.score.to_dict() if self.score else None,
            "results": [r.to_dict() for r in self.results],
            "privacy": self.privacy,
            "status": self.status,
            "error_detail": self.error_detail,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ScanReport":
        from .scoring import ScoreBreakdown

        return cls(
            url=d["url"],
            started_at=d["started_at"],
            finished_at=d["finished_at"],
            duration_s=d.get("duration_s", 0.0),
            results=[CheckResult.from_dict(r) for r in d.get("results", [])],
            score=ScoreBreakdown.from_dict(d["score"]) if d.get("score") else None,
            privacy=d.get("privacy"),
            status=d.get("status", "ok"),
            error_detail=d.get("error_detail", ""),
        )


async def _accessibility_gate(target: str):
    """KL-94 — o site é acessível? Retorna ``None`` se SIM (segue o scan), ou
    ``(status, error_detail)`` se NÃO. Duas etapas:
      1. **DNS** — o domínio resolve A/AAAA? NXDOMAIN → ``domain_not_found``; timeout/erro
         → ``dns_error`` (transitório).
      2. **HTTP** — o site responde? QUALQUER resposta (200/301/403/503) = acessível → segue
         (o `fetch` usa `verify=False`, então SSL inválido não aborta: o check_ssl marca FAIL).
         Falha de conexão (timeout/refused/DNS) → ``unreachable``.
    """
    from .checks.base import domain_of, fetch
    from .checks import dns_util

    domain = domain_of(target)
    dns_status = await asyncio.to_thread(dns_util.resolve_host_status, domain)
    if dns_status == "nxdomain":
        return ("domain_not_found", "Este domínio não foi encontrado no DNS.")
    if dns_status == "error":
        return ("dns_error", "Não foi possível consultar o DNS deste domínio.")

    try:
        await fetch(target, timeout=10)  # qualquer resposta HTTP = acessível
    except Exception:  # noqa: BLE001 - timeout/refused/erro de conexão → fora do ar
        return ("unreachable", "O site não respondeu. Pode estar fora do ar.")
    return None


async def run_scan(url: str, full: bool = True) -> ScanReport:
    """Run the registered checks against ``url`` concurrently and score them.

    ``full=True`` (padrão) roda todos os checks (tier pago, 29). ``full=False`` roda
    só o tier gratuito (15 primeiros, KL-27) — economiza tempo de scan e requests
    DNS/API do funil público.

    Os checks rodam em **paralelo** com teto ``SCAN_MAX_CONCURRENCY`` (KL-51 f3 hotfix).
    O rate limiter por-domínio (`base.fetch`) mantém 1 req/s por domínio — a regra do
    scanner passivo é preservada; a paralelização só sobrepõe checks de domínios
    distintos. ``asyncio.gather`` devolve os resultados **na ordem dos checks**.
    """
    target = normalize_url(url)
    loop = asyncio.get_event_loop()
    started_at = datetime.now(timezone.utc)
    t0 = loop.time()

    # KL-94 — GATE DE ACESSIBILIDADE: antes de rodar os 48 checks, confirma que o site é
    # acessível. Um domínio inexistente/offline NÃO pode receber score (os checks Tipo B
    # retornariam PASS falsos). Aborta cedo com um `status` claro.
    gate = await _accessibility_gate(target)
    if gate is not None:
        status, detail = gate
        finished_at = datetime.now(timezone.utc)
        return ScanReport(
            url=target, started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat(), duration_s=loop.time() - t0,
            results=[], score=None, status=status, error_detail=detail)

    checks = ALL_CHECKS if full else FREE_CHECKS
    sem = asyncio.Semaphore(SCAN_MAX_CONCURRENCY)

    async def _run_one(check_id: str, check_fn) -> CheckResult:
        try:
            async with sem:
                result = await check_fn(target)
        except Exception as exc:  # noqa: BLE001 - one bad check must not kill the scan
            result = CheckResult(
                name=check_id,
                status=Status.INCONCLUSO,
                severity=Severity.MEDIA,
                evidence=f"Erro inesperado ao executar o check: {exc!r}",
            )
        result.check_id = check_id
        # Carimba OWASP/CWE/LGPD (KL-34/35) — metadata, não afeta o score. Centralizado
        # aqui (onde o check_id é setado) para não editar as ~100 return sites dos checks.
        cls = classify(check_id)
        result.owasp, result.cwe, result.lgpd = cls.owasp, cls.cwe, cls.lgpd
        return result

    # KL-44 P5: os indicadores de privacidade rodam JUNTO (um único GET próprio, passivo)
    # e são independentes do score de segurança. Fail-open: erro → privacy=None.
    async def _privacy() -> Optional[dict]:
        try:
            from . import privacy_checks
            return await privacy_checks.scan_privacy(target)
        except Exception:  # noqa: BLE001
            return None

    # gather preserva a ordem de entrada → o relatório mantém a ordem dos checks.
    gathered = await asyncio.gather(
        *(_run_one(cid, fn) for cid, fn in checks), _privacy())
    results: List[CheckResult] = list(gathered[:-1])
    privacy = gathered[-1]

    duration = loop.time() - t0
    finished_at = datetime.now(timezone.utc)
    score = compute_score(results)  # privacidade NÃO entra no score de segurança

    return ScanReport(
        url=target,
        started_at=started_at.isoformat(),
        finished_at=finished_at.isoformat(),
        duration_s=duration,
        results=results,
        score=score,
        privacy=privacy,
    )


def scan(url: str, full: bool = True) -> ScanReport:
    """Synchronous convenience wrapper around :func:`run_scan`."""
    return asyncio.run(run_scan(url, full=full))


# --------------------------------------------------------------------------- #
# Pretty printing (used by the CLI / quick test)
# --------------------------------------------------------------------------- #

_STATUS_ICON = {
    Status.PASS: "✅ PASS",
    Status.FAIL: "❌ FAIL",
    Status.INCONCLUSO: "⚠️  INCONCLUSO",
}


def format_report(report: ScanReport) -> str:
    lines: List[str] = []
    lines.append("=" * 72)
    lines.append(f"  KLARIM — Relatório de varredura")
    lines.append(f"  Alvo:  {report.url}")
    lines.append(f"  Data:  {report.started_at}  ({report.duration_s:.1f}s)")
    lines.append("=" * 72)

    for i, r in enumerate(report.results, start=1):
        status = _STATUS_ICON.get(r.status, r.status)
        lines.append(f"{i:>2}. [{status}] ({r.severity})  {r.name}")
        lines.append(f"     ↳ {r.evidence}")

    if report.score is not None:
        s = report.score
        lines.append("-" * 72)
        lines.append(
            f"  SCORE: {s.score}/100  {s.grade_icon} ({s.semaphore.upper()})"
        )
        lines.append(
            f"  PASS: {s.passed}   FAIL: {s.failed}   "
            f"INCONCLUSO: {s.inconclusive}"
        )
        f = s.fails_by_severity
        lines.append(
            "  Falhas por severidade — "
            f"Crítica: {f.get(Severity.CRITICA, 0)}, "
            f"Alta: {f.get(Severity.ALTA, 0)}, "
            f"Média: {f.get(Severity.MEDIA, 0)}, "
            f"Baixa: {f.get(Severity.BAIXA, 0)}"
        )
    lines.append("=" * 72)
    return "\n".join(lines)
