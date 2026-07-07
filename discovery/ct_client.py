"""Client de Certificate Transparency (crt.sh) para descobrir domínios recentes.

Duas fontes: PostgreSQL público do crt.sh (preciso, com filtro por data) como
primário, e a API JSON como fallback. Ambos passam pelo mesmo filtro de ruído.
"""

from __future__ import annotations

import asyncio
from typing import List, Set

import httpx

from scanner.checks.base import registrable_domain

# Prefixos de subdomínio que são infra, não site de negócio.
_INFRA_PREFIXES = (
    "mail.", "smtp.", "imap.", "pop.", "webmail.", "autodiscover.", "autoconfig.",
    "api.", "admin.", "staging.", "stg.", "dev.", "test.", "hml.", "homolog.",
    "cpanel.", "whm.", "ns1.", "ns2.", "mx.", "vpn.", "cdn.", "static.", "assets.",
)


class CTClient:
    async def get_recent_domains(
        self, suffix: str = ".com.br", days: int = 7, limit: int = 500
    ) -> List[str]:
        raw: List[str] = []
        # crt.sh derruba conexões sob carga; algumas tentativas ajudam.
        for attempt in range(3):
            try:
                raw = await asyncio.wait_for(
                    asyncio.to_thread(self._from_postgres, suffix, days, limit), timeout=45
                )
                if raw:
                    break
            except Exception as exc:  # noqa: BLE001 - crt.sh Postgres é instável
                print(f"[ct] Postgres crt.sh tentativa {attempt + 1} falhou ({exc!r})", flush=True)
                await asyncio.sleep(2 * (attempt + 1))
        if not raw:
            raw = await self._from_json(suffix, limit)
        return self._filter(raw, suffix, limit)

    # --- fontes ------------------------------------------------------------ #

    def _from_postgres(self, suffix: str, days: int, limit: int) -> List[str]:
        import psycopg2

        # crt.sh está atrás de pgbouncer -> não aceita `options` no startup nem
        # SET persistente. Padrão reverso (rb.moc.%) evita o LIKE com wildcard à
        # esquerda (usa o índice reverso do crt.sh).
        reversed_pattern = suffix[::-1] + "%"
        conn = psycopg2.connect(
            host="crt.sh", port=5432, user="guest", dbname="certwatch",
            connect_timeout=15,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT ci.NAME_VALUE
                    FROM certificate_and_identities ci
                    WHERE reverse(lower(ci.NAME_VALUE)) LIKE %s
                      AND ci.NAME_TYPE = 'dNSName'
                      AND ci.NOT_BEFORE >= NOW() - (%s || ' days')::interval
                    LIMIT %s
                    """,
                    (reversed_pattern, str(days), limit * 3),
                )
                return [r[0] for r in cur.fetchall()]
        finally:
            conn.close()

    async def _from_json(self, suffix: str, limit: int) -> List[str]:
        url = f"https://crt.sh/?q=%25{suffix}&output=json&exclude=expired"
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.get(url, headers={"User-Agent": "KlarimDiscovery/0.1"})
            if resp.status_code != 200:
                return []
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"[ct] JSON API crt.sh falhou ({exc!r})", flush=True)
            return []
        names: List[str] = []
        for entry in data[: limit * 5]:
            nv = entry.get("name_value", "")
            names.extend(nv.split("\n"))
        return names

    # --- filtro de ruído --------------------------------------------------- #

    def _filter(self, raw: List[str], suffix: str, limit: int) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for name in raw:
            name = (name or "").strip().lower().lstrip("*.")
            if not name.endswith(suffix) or " " in name:
                continue
            if name.startswith(_INFRA_PREFIXES):
                continue
            reg = registrable_domain(name)
            if not reg.endswith(suffix) or reg in seen:
                continue
            seen.add(reg)
            out.append(reg)
            if len(out) >= limit:
                break
        return out
