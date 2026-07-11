"""Helpers de DNS (dnspython) para os checks de e-mail/infra (KL-22).

Funções **síncronas** (os checks as chamam via ``asyncio.to_thread``) e
**mockáveis** (os testes monkeypatcham estas funções, mantendo o CI hermético).

Convenção de retorno:
- `resolve_txt`: lista de strings TXT; `[]` = domínio existe mas sem o record
  (ausência **definitiva** → FAIL); `None` = erro de DNS (timeout/sem nameserver
  → INCONCLUSO). NXDOMAIN/NoAnswer contam como `[]` (ausência definitiva).
- `resolve_cname`: alvo do CNAME (str) ou `None` (sem CNAME/erro).
- `host_exists`: `True`/`False` (NXDOMAIN) ou `None` (erro).
"""

from __future__ import annotations

from typing import List, Optional


def _resolver(timeout: float):
    import dns.resolver

    r = dns.resolver.Resolver()
    r.lifetime = timeout
    r.timeout = timeout
    return r


def resolve_txt(name: str, timeout: float = 5.0) -> Optional[List[str]]:
    try:
        import dns.resolver  # noqa: F401
    except ImportError:
        return None
    import dns.resolver as _r
    try:
        answers = _resolver(timeout).resolve(name, "TXT")
        out: List[str] = []
        for rr in answers:
            out.append(b"".join(rr.strings).decode("utf-8", "replace"))
        return out
    except (_r.NXDOMAIN, _r.NoAnswer):
        return []  # ausência definitiva
    except Exception:  # noqa: BLE001 - timeout/no nameservers -> incerto
        return None


def resolve_cname(name: str, timeout: float = 3.0) -> Optional[str]:
    try:
        import dns.resolver  # noqa: F401
    except ImportError:
        return None
    try:
        ans = _resolver(timeout).resolve(name, "CNAME")
        return str(ans[0].target).rstrip(".").lower()
    except Exception:  # noqa: BLE001 - sem CNAME ou erro
        return None


def host_exists(name: str, timeout: float = 3.0) -> Optional[bool]:
    try:
        import dns.resolver  # noqa: F401
    except ImportError:
        return None
    import dns.resolver as _r
    try:
        _resolver(timeout).resolve(name, "A")
        return True
    except _r.NXDOMAIN:
        return False
    except _r.NoAnswer:
        return True  # o nome existe (só não tem A) — não é dangling
    except Exception:  # noqa: BLE001
        return None


def resolve_mx(name: str, timeout: float = 5.0) -> Optional[List[str]]:
    """Hostnames dos registros MX (mapeamento de provedor de e-mail — KL-50).
    `[]` = sem MX; `None` = erro de DNS. Mockável nos testes."""
    try:
        import dns.resolver  # noqa: F401
    except ImportError:
        return None
    import dns.resolver as _r
    try:
        answers = _resolver(timeout).resolve(name, "MX")
        return [str(rr.exchange).rstrip(".").lower() for rr in answers]
    except (_r.NXDOMAIN, _r.NoAnswer):
        return []
    except Exception:  # noqa: BLE001
        return None


def resolve_ns(name: str, timeout: float = 5.0) -> Optional[List[str]]:
    """Hostnames dos registros NS (mapeamento de provedor de DNS — KL-50)."""
    try:
        import dns.resolver  # noqa: F401
    except ImportError:
        return None
    import dns.resolver as _r
    try:
        answers = _resolver(timeout).resolve(name, "NS")
        return [str(rr.target).rstrip(".").lower() for rr in answers]
    except (_r.NXDOMAIN, _r.NoAnswer):
        return []
    except Exception:  # noqa: BLE001
        return None
