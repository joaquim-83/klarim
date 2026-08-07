"""KL-141 — checks de EXPOSIÇÃO (KL-139 checks 1-3, 5-12): arquivos de config, .git,
painéis admin, docs de API, debug, backups, source maps, directory listing, .htaccess e
server-status acessíveis SEM auth após o deploy.

Princípio (KL-139): verificar **acessibilidade**, NUNCA conteúdo. HEAD primeiro (não baixa
body); GET só p/ distinguir directory-listing real de fallback de SPA (limitado a 2000 chars).
Um path exposto no grupo já reprova o grupo (`break` — não testa o resto)."""
from __future__ import annotations

from typing import List, Optional

import httpx

from ..models import Result, Severity, Status
from ..utils import matches_spa_fingerprint

# Cada grupo = um check do KL-139 (paths + severidade + template de detalhe).
EXPOSURE_PATHS = [
    # Check 1 — .env (Crítico)
    {"check": "env_exposed",
     "paths": ["/.env", "/.env.local", "/.env.production", "/.env.backup", "/.env.example"],
     "severity": Severity.CRITICAL, "detail": "Arquivo de configuração {path} acessível"},

    # Check 2 — .git (Crítico)
    {"check": "git_exposed", "paths": ["/.git/config", "/.git/HEAD"],
     "severity": Severity.CRITICAL, "detail": "Repositório git {path} acessível"},

    # Check 3 — Config de CMS (Crítico)
    {"check": "cms_config_exposed",
     "paths": ["/wp-config.php", "/wp-config.php.bak", "/wp-config.php.old",
               "/configuration.php", "/config/database.yml", "/config.php"],
     "severity": Severity.CRITICAL, "detail": "Configuração de CMS {path} acessível"},

    # Check 5 — Painéis administrativos (Alta)
    {"check": "admin_panel_exposed",
     "paths": ["/admin", "/wp-admin", "/administrator", "/phpmyadmin",
               "/adminer", "/cpanel", "/admin.php"],
     "severity": Severity.HIGH, "detail": "Painel administrativo {path} acessível"},

    # Check 6 — Documentação de API (Alta)
    {"check": "api_docs_exposed",
     "paths": ["/swagger", "/swagger-ui", "/swagger-ui.html", "/docs", "/redoc",
               "/graphql", "/api-docs", "/openapi.json", "/v2/api-docs", "/v3/api-docs"],
     "severity": Severity.HIGH, "detail": "Documentação de API {path} acessível"},

    # Check 7 — Debug/profiler (Alta)
    {"check": "debug_exposed",
     "paths": ["/debug", "/_debug", "/_profiler", "/elmah.axd",
               "/trace.axd", "/phpinfo.php", "/info.php"],
     "severity": Severity.HIGH, "detail": "Debug/profiler {path} ativo"},

    # Check 8 — Backup/dump (Alta)
    {"check": "backup_exposed",
     "paths": ["/dump.sql", "/database.sql", "/backup/", "/backup.sql",
               "/db.sql", "/data.sql"],
     "severity": Severity.HIGH, "detail": "Backup/dump {path} acessível"},

    # Check 9 — Source maps (Média)
    {"check": "source_maps_exposed",
     "paths": ["/main.js.map", "/app.js.map", "/bundle.js.map",
               "/static/js/main.js.map", "/assets/index.js.map"],
     "severity": Severity.MEDIUM,
     "detail": "Source map {path} acessível — código-fonte do frontend exposto"},

    # Check 10 — Directory listing (Média)
    {"check": "directory_listing",
     "paths": ["/uploads/", "/media/", "/files/", "/static/"],
     "severity": Severity.MEDIUM, "detail": "Listagem de diretório {path} acessível"},

    # Check 11 — .htaccess/.htpasswd (Média)
    {"check": "htaccess_exposed", "paths": ["/.htaccess", "/.htpasswd"],
     "severity": Severity.MEDIUM,
     "detail": "Arquivo de configuração Apache {path} acessível"},

    # Check 12 — server-status/info (Média)
    {"check": "server_info_exposed", "paths": ["/server-status", "/server-info"],
     "severity": Severity.MEDIUM, "detail": "Informações do servidor {path} acessíveis"},
]


# KL-141 (Prompt 3) — extensões cujo recurso REAL nunca é HTML (source map, dump SQL, JSON/YAML de
# config, .env, backup). Um 200 `text/html` nelas = fallback de SPA/app (que devolve o index.html
# p/ qualquer path), NÃO o arquivo real → não é exposição. `.php`/`.axd` ficam DE FORA (phpinfo/
# elmah reais SÃO HTML — suprimi-los seria falso NEGATIVO, pior que o falso positivo). Só o
# Content-Type do HEAD é lido (sem body), fiel ao princípio HEAD-first.
_NON_HTML_EXT = (".env", ".map", ".sql", ".json", ".yml", ".yaml", ".bak", ".old", ".config")


def _is_spa_fallback_nonhtml(path: str, response: httpx.Response) -> bool:
    if not path.lower().rstrip("/").endswith(_NON_HTML_EXT):
        return False
    return "text/html" in (response.headers.get("content-type") or "").lower()


def _is_directory_listing(html: str) -> bool:
    """Heurística: distingue uma listagem de diretório REAL de um fallback de SPA (que
    devolve 200 + HTML da app para qualquer path). True só se o corpo tem marcadores de
    listagem (Apache/nginx autoindex, IIS). Case-insensitive."""
    lower = html.lower()
    return any(marker in lower for marker in (
        "index of /", "directory listing", "<pre>", "[to parent directory]",
        "last modified", "name</a>", "size</a>",
    ))


async def check_exposure(client: httpx.AsyncClient, base_url: str, config=None,
                         spa_fingerprint: Optional[dict] = None) -> List[Result]:
    """Verifica todos os grupos de exposição. HEAD primeiro; GET só p/ directory-listing.

    3 guards contra falso positivo de SPA (app que devolve 200+index.html p/ qualquer path):
      1. `config.exposure_allowlist` — pula paths conhecidos (não testa nem gasta request).
      2. **`spa_fingerprint` (KL-147)** — se o 200 casa o fingerprint do probe de controle
         (mesmo ETag/CT+CL do index.html), é fallback → PASS. Cobre paths SEM extensão
         (`/admin`, `/swagger`) que o guard 3 não pega.
      3. `_is_spa_fallback_nonhtml` (KL-141 P3) — recurso de extensão não-HTML servindo text/html."""
    base = base_url.rstrip("/")
    allowlist = set(getattr(config, "exposure_allowlist", None) or [])
    results: List[Result] = []

    for group in EXPOSURE_PATHS:
        group_passed = True
        for path in group["paths"]:
            if path in allowlist:
                continue   # allowlisted → não testa (não vira FAIL nem gasta request)
            url = f"{base}{path}"
            try:
                # HEAD primeiro: confere acessibilidade sem baixar o body (princípio KL-139).
                r = await client.head(url)
                if r.status_code == 200:
                    # Guard KL-147: mesmo fingerprint do probe → fallback de SPA (não exposição).
                    if matches_spa_fingerprint(r, spa_fingerprint):
                        continue
                    if group["check"] == "directory_listing":
                        # Pode ser um fallback de SPA (200 p/ tudo). Um GET limitado
                        # distingue: só é FAIL se o corpo tiver marcadores de listagem.
                        body = (await client.get(url)).text[:2000]
                        if not _is_directory_listing(body):
                            continue  # SPA fallback, não é listagem real
                    elif _is_spa_fallback_nonhtml(path, r):
                        continue  # recurso não-HTML devolvendo text/html = fallback de SPA/app
                    results.append(Result(
                        check=group["check"], category="exposure", path=path,
                        status=Status.FAIL, severity=group["severity"],
                        detail=group["detail"].format(path=path), http_status=200))
                    group_passed = False
                    break  # um path exposto no grupo já basta
            except httpx.TimeoutException:
                results.append(Result(
                    check=group["check"], category="exposure", path=path,
                    status=Status.ERROR, severity=Severity.INFO,
                    detail=f"Timeout ao verificar {path}"))
            except httpx.HTTPError:
                pass  # conexão recusada/DNS/etc. = path não acessível ≈ PASS

        if group_passed:
            results.append(Result(
                check=group["check"], category="exposure", path=group["paths"][0],
                status=Status.PASS, severity=group["severity"],
                detail=f"{group['check']}: nenhum path exposto"))

    return results
