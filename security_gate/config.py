"""KL-141 (Prompt 3) — configuração do Security Gate: `GateConfig` (dataclass) + `load_config`
(YAML → defaults, sobrescrito pelos args da CLI).

O `import yaml` é **lazy** (dentro de `load_config`) — o núcleo do Gate (engine/checks) continua
importável sem `pyyaml`; a dependência só entra quando se carrega um arquivo YAML de fato."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class GateConfig:
    target: str = ""
    fail_on: str = "critical"
    timeout: int = 60
    # KL-149 — default inclui os 18 checks (5 do KL-141 + 13 novos). Ordem de execução é do engine.
    checks: List[str] = field(default_factory=lambda: [
        "headers", "ssl", "exposure", "credentials", "api",
        "cors", "cookies", "https_redirect", "open_redirect", "error_disclosure", "jwt",
        "form_security", "dns", "dependencies", "tls_ciphers", "subdomain", "infrastructure",
        "rate_limit"])

    # Headers
    hsts_min_age: int = 31_536_000

    # SSL
    ssl_min_days: int = 14

    # Exposure — allowlist resolve os falsos positivos de SPA (Prompt 1: /docs, /adminer, …).
    exposure_allowlist: List[str] = field(default_factory=list)
    exposure_extra_paths: List[dict] = field(default_factory=list)

    # API security
    api_root_path: str = "/api/"
    protected_endpoints: List[str] = field(default_factory=list)

    # KL-149 — endpoints testados pelo check de rate limit (mini-burst de 10 requests cada).
    rate_limit_endpoints: List[str] = field(default_factory=lambda: ["/", "/api/"])

    # Credentials
    credential_extra_patterns: List[dict] = field(default_factory=list)

    # Notifications (usadas no Prompt 4)
    email_recipients: List[str] = field(default_factory=list)
    email_on: str = "failure"
    webhook_url: str = ""
    webhook_on: str = "failure"


def _apply_yaml(config: GateConfig, data: dict) -> None:
    """Preenche o `config` a partir do dict YAML (best-effort; chaves ausentes ficam no default)."""
    config.target = data.get("target", config.target)
    config.fail_on = data.get("fail_on", config.fail_on)
    config.timeout = data.get("timeout", config.timeout)

    checks_data = data.get("checks") or {}
    if isinstance(checks_data, dict):
        # Desabilita um check com `enabled: false`.
        for name, cfg in checks_data.items():
            if isinstance(cfg, dict) and not cfg.get("enabled", True):
                config.checks = [c for c in config.checks if c != name]

        headers_cfg = checks_data.get("headers") or {}
        if isinstance(headers_cfg, dict):
            config.hsts_min_age = headers_cfg.get("hsts_min_age", config.hsts_min_age)

        ssl_cfg = checks_data.get("ssl") or {}
        if isinstance(ssl_cfg, dict):
            config.ssl_min_days = ssl_cfg.get("min_days", config.ssl_min_days)

        exposure_cfg = checks_data.get("exposure") or {}
        if isinstance(exposure_cfg, dict):
            config.exposure_allowlist = list(exposure_cfg.get("allowlist", []) or [])
            config.exposure_extra_paths = list(exposure_cfg.get("extra_paths", []) or [])

        api_cfg = checks_data.get("api") or {}
        if isinstance(api_cfg, dict):
            config.api_root_path = api_cfg.get("root_path", config.api_root_path)
            config.protected_endpoints = list(api_cfg.get("protected_endpoints", []) or [])

        cred_cfg = checks_data.get("credentials") or {}
        if isinstance(cred_cfg, dict):
            config.credential_extra_patterns = list(cred_cfg.get("extra_patterns", []) or [])

        rl_cfg = checks_data.get("rate_limit") or {}   # KL-149
        if isinstance(rl_cfg, dict) and rl_cfg.get("endpoints"):
            config.rate_limit_endpoints = list(rl_cfg.get("endpoints") or [])

    notif = data.get("notifications") or {}
    if isinstance(notif, dict):
        email_cfg = notif.get("email") or {}
        if isinstance(email_cfg, dict):
            config.email_recipients = list(email_cfg.get("recipients", []) or [])
            config.email_on = email_cfg.get("on", config.email_on)
        webhook_cfg = notif.get("webhook") or {}
        if isinstance(webhook_cfg, dict):
            config.webhook_url = webhook_cfg.get("url", config.webhook_url)
            config.webhook_on = webhook_cfg.get("on", config.webhook_on)


def load_config(yaml_path: str = "security-gate.yml", args=None) -> GateConfig:
    """Config do YAML (se existir) sobrescrita pelos args da CLI. YAML ausente → defaults."""
    config = GateConfig()

    if yaml_path and os.path.exists(yaml_path):
        import yaml  # lazy: só quando há YAML de verdade a carregar
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if isinstance(data, dict):
            _apply_yaml(config, data)

    # Args da CLI têm precedência sobre o YAML.
    if args is not None:
        if getattr(args, "url", None):
            config.target = args.url
        if getattr(args, "fail_on", None):
            config.fail_on = args.fail_on
        if getattr(args, "timeout", None):
            config.timeout = args.timeout
        if getattr(args, "checks", None):
            config.checks = [c.strip() for c in args.checks.split(",") if c.strip()]

    return config
