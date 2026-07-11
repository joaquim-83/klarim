"""Mensagens de risco concretas por check (KL-20).

Substitui o bloco fixo de LGPD por consequências reais, em linguagem de dono de
negócio (sem jargão, sem artigos de lei). Indexado pelo `check_id` do scanner.

Módulo **sem dependências pesadas** — pode ser importado pelo reporter (PDF), pela
API, pelos e-mails (notifier) e pelos workers (discovery) sem puxar WeasyPrint.
"""

from __future__ import annotations

from typing import Any, Dict, List

# check_id -> {headline, risk, icon}
RISK_MESSAGES: Dict[str, Dict[str, str]] = {
    "check_01_https": {
        "headline": "Seu site funciona sem criptografia",
        "risk": "Qualquer pessoa na mesma rede (Wi-Fi de café, hotel, aeroporto) pode ver "
                "tudo que seus clientes digitam — senhas, dados pessoais, números de cartão.",
        "icon": "🔓",
    },
    "check_02_hsts": {
        "headline": "Seu site não força conexão segura",
        "risk": "Na primeira visita, o navegador do cliente pode ser interceptado e "
                "redirecionado para uma página falsa idêntica à sua — sem que ele perceba.",
        "icon": "⚠️",
    },
    "check_03_ssl": {
        "headline": "Seu certificado de segurança está inválido",
        "risk": "Os navegadores mostram um aviso vermelho de 'site não seguro' para seus "
                "clientes. Muitos vão desistir de acessar — e os que insistirem ficam vulneráveis.",
        "icon": "🚫",
    },
    "check_04_tls": {
        "headline": "Seu site aceita criptografia antiga e falha",
        "risk": "Versões obsoletas de criptografia (TLS 1.0/1.1) têm brechas conhecidas que "
                "permitem interceptar e ler a comunicação entre seus clientes e o site.",
        "icon": "🔐",
    },
    "check_05_csp": {
        "headline": "Seu site não tem proteção contra scripts maliciosos",
        "risk": "Um atacante pode injetar código invisível nas suas páginas para roubar dados "
                "dos clientes, redirecionar para golpes, ou instalar vírus nos dispositivos de "
                "quem acessa.",
        "icon": "💉",
    },
    "check_06_xfo": {
        "headline": "Seu site pode ser embutido em páginas falsas",
        "risk": "Um golpista pode criar uma página que mostra o seu site por cima de botões "
                "invisíveis. O cliente pensa que está interagindo com você, mas está clicando "
                "em ações controladas pelo atacante.",
        "icon": "🖼️",
    },
    "check_07_xcto": {
        "headline": "Seu servidor não protege contra interpretação maliciosa de arquivos",
        "risk": "Arquivos que deveriam ser inofensivos (imagens, textos) podem ser executados "
                "como código pelo navegador, abrindo porta para ataques.",
        "icon": "📄",
    },
    "check_08_server": {
        "headline": "Seu servidor revela a versão exata do software que usa",
        "risk": "É como mostrar a marca e o modelo da fechadura da sua porta. O invasor não "
                "precisa adivinhar — sabe exatamente quais falhas conhecidas explorar.",
        "icon": "🔍",
    },
    "check_09_sourcemaps": {
        "headline": "O código-fonte do seu site está exposto publicamente",
        "risk": "É como publicar a planta da sua casa com a localização do cofre. Um atacante "
                "pode ler toda a lógica do sistema e encontrar pontos de entrada.",
        "icon": "📋",
    },
    "check_10_sensitive": {
        "headline": "Arquivos de configuração do seu site estão acessíveis",
        "risk": "Esses arquivos podem conter senhas, chaves de acesso ao banco de dados e "
                "informações internas. Qualquer pessoa na internet pode baixá-los.",
        "icon": "🗝️",
    },
    "check_11_dirlist": {
        "headline": "As pastas do seu servidor estão abertas para navegação",
        "risk": "Qualquer pessoa pode ver todos os arquivos do seu servidor — como deixar o "
                "armário de documentos aberto no meio da calçada.",
        "icon": "📂",
    },
    "check_12_metatags": {
        "headline": "Seu site revela o framework de desenvolvimento",
        "risk": "Isso facilita ataques automatizados — robôs de hackers varrem a internet "
                "buscando exatamente esse tipo de site porque conhecem as falhas comuns.",
        "icon": "🏷️",
    },
    "check_13_sri": {
        "headline": "Scripts de terceiros são carregados sem verificação",
        "risk": "Seu site confia cegamente em código externo. Se qualquer uma dessas fontes "
                "for invadida, o código malicioso roda automaticamente no seu site — roubando "
                "dados dos seus clientes sem você saber.",
        "icon": "🔗",
    },
    "check_14_risky_sources": {
        "headline": "Seu site carrega código de fontes não confiáveis",
        "risk": "O código vem de um local que qualquer pessoa pode modificar. Isso pode ser "
                "usado para roubar senhas, dados de cartão, ou redirecionar seus clientes para "
                "golpes — a qualquer momento.",
        "icon": "⚡",
    },
    "check_15_external_domains": {
        "headline": "Seu site depende de muitos serviços externos",
        "risk": "Quanto mais portas abertas, maior o risco. Se qualquer um desses serviços for "
                "comprometido, seu site é afetado diretamente — e seus clientes também.",
        "icon": "🌐",
    },
    "check_16_api_docs": {
        "headline": "A documentação da sua API está exposta publicamente",
        "risk": "A documentação completa da sua API está acessível. Um atacante pode mapear "
                "todos os endpoints e parâmetros do seu sistema.",
        "icon": "📖",
    },
    "check_17_cookies": {
        "headline": "Cookies do seu site não têm proteção",
        "risk": "Cookies do seu site podem ser roubados por scripts maliciosos e usados para "
                "se passar por seus clientes.",
        "icon": "🍪",
    },
    "check_18_cors": {
        "headline": "Qualquer site pode chamar a sua API",
        "risk": "Qualquer site na internet pode fazer requisições ao seu servidor e acessar "
                "dados dos seus clientes.",
        "icon": "🌍",
    },
    "check_19_redirect_domain": {
        "headline": "Seu site redireciona para outro domínio",
        "risk": "Se o domínio original expirar, qualquer pessoa pode registrá-lo e se passar "
                "por você.",
        "icon": "↪️",
    },
    "check_20_info_disclosure": {
        "headline": "Seu servidor confirma a existência de arquivos internos",
        "risk": "Seu servidor confirma a existência de arquivos internos mesmo ao bloqueá-los. "
                "Um atacante sabe exatamente o que procurar.",
        "icon": "🔎",
    },
    "check_21_spf": {
        "headline": "Qualquer um pode enviar e-mail fingindo ser você",
        "risk": "Qualquer pessoa na internet pode enviar e-mails fingindo ser do seu domínio. "
                "Seus clientes podem receber golpes com o nome da sua empresa.",
        "icon": "📧",
    },
    "check_22_dkim": {
        "headline": "Seus e-mails não têm assinatura digital",
        "risk": "E-mails enviados pelo seu domínio não têm assinatura digital. Provedores como "
                "Gmail podem marcá-los como suspeitos ou spam.",
        "icon": "✉️",
    },
    "check_23_dmarc": {
        "headline": "Seu domínio não tem proteção contra phishing",
        "risk": "Golpistas podem enviar e-mails como se fossem da sua empresa e seus clientes "
                "não têm como distinguir.",
        "icon": "🎣",
    },
    "check_24_mixed_content": {
        "headline": "Seu site seguro carrega arquivos de fontes inseguras",
        "risk": "Seu site seguro (HTTPS) carrega arquivos de fontes inseguras (HTTP). Esses "
                "arquivos podem ser interceptados e substituídos por código malicioso.",
        "icon": "🔀",
    },
    "check_25_form_security": {
        "headline": "Formulários do seu site enviam dados sem criptografia",
        "risk": "Qualquer pessoa na mesma rede pode interceptar senhas, e-mails e dados "
                "pessoais que seus clientes digitam nos formulários.",
        "icon": "📝",
    },
    "check_26_subdomains": {
        "headline": "Seus ambientes internos estão visíveis publicamente",
        "risk": "Ambientes internos (staging, admin, API) estão visíveis publicamente. Um "
                "atacante pode usá-los como porta de entrada — eles costumam ter menos proteção.",
        "icon": "🕸️",
    },
    "check_27_dangling_cname": {
        "headline": "Um subdomínio seu pode ser sequestrado",
        "risk": "Um subdomínio aponta para um serviço que não existe mais. Qualquer pessoa pode "
                "registrá-lo e publicar conteúdo como se fosse você.",
        "icon": "👻",
    },
    "check_28_hibp": {
        "headline": "Seu domínio aparece em vazamentos de dados",
        "risk": "O domínio da sua empresa aparece em vazamentos conhecidos. Credenciais de "
                "funcionários ou clientes podem estar circulando na internet.",
        "icon": "💧",
    },
    "check_29_safe_browsing": {
        "headline": "O Google marcou seu site como perigoso",
        "risk": "Navegadores mostram um alerta vermelho antes de seus clientes acessarem — seu "
                "site perde visitas e credibilidade.",
        "icon": "☠️",
    },
    "check_30_vulnerable_components": {
        "headline": "Seu site usa ferramentas com falhas conhecidas",
        "risk": "É como dirigir um carro que teve vários recalls e você nunca levou na oficina: "
                "as falhas já são públicas e qualquer atacante sabe exatamente como entrar. "
                "A correção costuma ser simples — atualizar as ferramentas.",
        "icon": "🚗",
    },
}

_SEV_ORDER = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAIXA": 3}

# Categorias de risco (por check_id) para compor a frase-resumo.
_CAT_VAZAMENTO = {"check_01_https", "check_02_hsts", "check_04_tls",
                  "check_09_sourcemaps", "check_10_sensitive",
                  "check_17_cookies", "check_18_cors", "check_25_form_security",
                  "check_28_hibp"}
_CAT_GOLPES = {"check_05_csp", "check_06_xfo", "check_14_risky_sources",
               "check_19_redirect_domain", "check_21_spf", "check_22_dkim",
               "check_23_dmarc", "check_27_dangling_cname", "check_29_safe_browsing"}
_CAT_INVASAO = {"check_08_server", "check_11_dirlist", "check_10_sensitive",
                "check_16_api_docs", "check_20_info_disclosure", "check_26_subdomains"}
_CAT_SUPPLY = {"check_13_sri", "check_14_risky_sources", "check_15_external_domains",
               "check_24_mixed_content", "check_30_vulnerable_components"}


def _get(item: Any, key: str) -> Any:
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _extract_results(x: Any) -> List[Any]:
    """Aceita ScanReport (`.results`), dict/`to_dict()` (`['results']`) ou uma lista."""
    if x is None:
        return []
    if hasattr(x, "results"):
        return x.results or []
    if isinstance(x, dict):
        return x.get("results", []) or []
    if hasattr(x, "to_dict"):
        return (x.to_dict() or {}).get("results", []) or []
    if isinstance(x, list):
        return x
    return []


def get_risk_messages(report_or_results: Any, limit: int = 4) -> List[Dict[str, str]]:
    """Riscos concretos dos FAILs de um scan, ordenados por severidade (máx `limit`).

    Aceita um ScanReport (usa `.results`), a lista de resultados (objetos ou dicts
    do `to_dict()`), ou o dict do report. Sem FAILs → lista vazia.
    """
    results = _extract_results(report_or_results)
    fails = [r for r in results if _get(r, "status") == "FAIL"]
    fails.sort(key=lambda r: _SEV_ORDER.get(_get(r, "severity"), 99))

    out: List[Dict[str, str]] = []
    for r in fails:
        cid = _get(r, "check_id")
        msg = RISK_MESSAGES.get(cid)
        if not msg:
            continue
        out.append({
            "icon": msg["icon"], "headline": msg["headline"], "risk": msg["risk"],
            "check_id": cid, "severity": _get(r, "severity"),
        })
        if len(out) >= limit:
            break
    return out


def get_risk_summary(risk_messages: List[Dict[str, str]]) -> str:
    """Frase-resumo dos riscos, a partir das categorias presentes."""
    if not risk_messages:
        return ""
    ids = {r.get("check_id") for r in risk_messages}
    phrases: List[str] = []
    if ids & _CAT_VAZAMENTO:
        phrases.append("vazamento de dados dos seus clientes")
    if ids & _CAT_GOLPES:
        phrases.append("uso do seu site para golpes")
    if ids & _CAT_INVASAO:
        phrases.append("invasão do servidor")
    if ids & _CAT_SUPPLY:
        phrases.append("código malicioso vindo de terceiros")

    if not phrases:
        return "Seu site não tem proteções básicas contra ataques comuns."
    if len(phrases) == 1:
        return f"Seu site apresenta risco de {phrases[0]}."
    return "Seu site apresenta riscos de " + ", ".join(phrases[:-1]) + " e " + phrases[-1] + "."
