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
    "check_31_permissions_policy": {
        "headline": "Seu site não restringe câmera, microfone e localização",
        "risk": "Qualquer script no seu site pode acessar câmera, microfone e localização do "
                "visitante sem pedir permissão adicional — inclusive scripts de terceiros.",
        "icon": "🎥",
    },
    "check_32_coop": {
        "headline": "Falta uma proteção moderna entre janelas",
        "risk": "Seu site não isola janelas abertas de/para outros sites. É uma camada moderna "
                "de segurança que os navegadores oferecem, mas que precisa ser ativada.",
        "icon": "🪟",
    },
    "check_33_coep": {
        "headline": "Falta o isolamento de recursos de outros sites",
        "risk": "Seu site não exige que recursos carregados de terceiros sejam explicitamente "
                "autorizados — outra camada moderna de proteção que não está ativada.",
        "icon": "🧩",
    },
    "check_34_corp": {
        "headline": "Seus recursos podem ser usados por qualquer site",
        "risk": "Qualquer outro site pode carregar imagens, scripts e arquivos do seu site. "
                "Ativar essa proteção limita quem pode consumir os seus recursos.",
        "icon": "🔗",
    },
    "check_35_referrer_policy": {
        "headline": "Seu site pode vazar endereços ao clicar em links",
        "risk": "Quando alguém clica num link do seu site para outro site, o endereço completo "
                "— incluindo dados na barra de endereço — pode ser enviado ao outro site.",
        "icon": "↗️",
    },
    "check_36_cache_control_forms": {
        "headline": "Formulários podem ficar guardados no cache",
        "risk": "Páginas com formulários do seu site podem ser armazenadas no cache do navegador. "
                "Num computador público, a próxima pessoa pode ver os dados preenchidos.",
        "icon": "💾",
    },
    "check_37_dnssec": {
        "headline": "As respostas de DNS do seu domínio não são autenticadas",
        "risk": "Sem essa proteção (DNSSEC), alguém poderia redirecionar seus visitantes para "
                "uma cópia falsa do seu site sem que ninguém percebesse.",
        "icon": "🧭",
    },
    "check_38_caa": {
        "headline": "Qualquer empresa pode emitir um certificado do seu site",
        "risk": "Seu domínio não define quais autoridades podem emitir certificados de segurança "
                "para ele. Qualquer empresa do mundo poderia criar um certificado falso do seu site.",
        "icon": "📜",
    },
    "check_39_mta_sts": {
        "headline": "E-mails do seu domínio podem ser interceptados no caminho",
        "risk": "Sem TLS obrigatório (MTA-STS), os e-mails podem trafegar sem criptografia. "
                "É como enviar uma carta sem lacrar o envelope — qualquer carteiro pode ler.",
        "icon": "✉️",
    },
    "check_40_bimi": {
        "headline": "Seus e-mails não exibem o logo da empresa",
        "risk": "Quando seus clientes recebem um e-mail seu, veem um ícone genérico em vez do seu "
                "logo — isso reduz a confiança e dificulta identificar e-mails falsos.",
        "icon": "🏷️",
    },
    "check_41_cipher_suites": {
        "headline": "A criptografia do seu site usa um algoritmo quebrado",
        "risk": "É como trancar a porta com uma fechadura que qualquer chaveiro abre em 5 "
                "segundos. Os dados dos seus clientes podem ser decifrados por quem interceptar.",
        "icon": "🔓",
    },
    "check_42_cert_chain": {
        "headline": "O certificado de segurança tem um problema de configuração",
        "risk": "A cadeia de confiança do seu certificado está incompleta ou inválida — alguns "
                "navegadores podem mostrar aviso de 'site não seguro' aos seus visitantes.",
        "icon": "🔗",
    },
    "check_43_ocsp_stapling": {
        "headline": "Seu site não confirma a validade do certificado a cada conexão",
        "risk": "Sem essa verificação embutida, a conexão fica mais lenta e a navegação dos seus "
                "clientes é revelada à autoridade certificadora.",
        "icon": "📶",
    },
    "check_44_key_strength": {
        "headline": "A chave de criptografia do seu site é fraca",
        "risk": "A chave é curta demais para os padrões atuais — é como ter uma senha de 4 "
                "caracteres. Um atacante pode quebrá-la e se passar pelo seu site.",
        "icon": "🗝️",
    },
    "check_45_html_comments": {
        "headline": "O código do seu site revela informações internas",
        "risk": "Encontramos comentários no código que expõem nomes de servidores e caminhos de "
                "arquivos. Um atacante usa isso para planejar um ataque mais preciso.",
        "icon": "📝",
    },
    "check_46_debug_mode": {
        "headline": "Seu site mostra informações técnicas quando dá erro",
        "risk": "É como um cofre que mostra a combinação escrita na porta quando alguém erra a "
                "senha. Os erros expõem a estrutura interna do seu site a qualquer visitante.",
        "icon": "🐛",
    },
    "check_47_open_redirect": {
        "headline": "Links do seu site podem levar a páginas falsas",
        "risk": "Um atacante pode criar um link que começa com o endereço do seu site mas leva "
                "para uma página maliciosa — o cliente confia porque vê o seu domínio.",
        "icon": "↪️",
    },
    "check_48_password_fields": {
        "headline": "O campo de senha pode ser salvo pelo navegador",
        "risk": "Se um cliente acessa seu site de um computador público (lan house, hotel), a "
                "próxima pessoa pode ver a senha salva pelo navegador.",
        "icon": "🔑",
    },
}

_SEV_ORDER = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAIXA": 3}

# Categorias de risco (por check_id) para compor a frase-resumo.
_CAT_VAZAMENTO = {"check_01_https", "check_02_hsts", "check_04_tls",
                  "check_09_sourcemaps", "check_10_sensitive",
                  "check_17_cookies", "check_18_cors", "check_25_form_security",
                  "check_28_hibp", "check_31_permissions_policy",
                  "check_35_referrer_policy", "check_36_cache_control_forms",
                  "check_39_mta_sts", "check_41_cipher_suites", "check_42_cert_chain",
                  "check_43_ocsp_stapling", "check_44_key_strength",
                  "check_48_password_fields"}
_CAT_GOLPES = {"check_05_csp", "check_06_xfo", "check_14_risky_sources",
               "check_19_redirect_domain", "check_21_spf", "check_22_dkim",
               "check_23_dmarc", "check_27_dangling_cname", "check_29_safe_browsing",
               "check_32_coop", "check_33_coep", "check_34_corp",
               "check_37_dnssec", "check_38_caa", "check_40_bimi",
               "check_47_open_redirect"}
_CAT_INVASAO = {"check_08_server", "check_11_dirlist", "check_10_sensitive",
                "check_16_api_docs", "check_20_info_disclosure", "check_26_subdomains",
                "check_45_html_comments", "check_46_debug_mode"}
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


# --------------------------------------------------------------------------- #
# KL-20 (fase 2) — dimensão SETORIAL + benchmark. As PMEs reagem a consequências
# concretas para o negócio delas, não a LGPD abstrata. Com o setor (KL-55) e o
# benchmark (KL-74/P5), as mensagens ficam específicas. Módulo puro — o benchmark
# é passado PELO chamador (sem tocar o banco aqui).
# --------------------------------------------------------------------------- #

# Contexto de risco por setor: `data_risk` (consequência), `audience` (quem é afetado),
# `plural` (como chamar os pares do setor — usado no CTA "compare com outros …").
DEFAULT_RISK = {
    "data_risk": "Dados dos seus clientes e do seu negócio podem estar expostos.",
    "audience": "clientes", "plural": "sites",
}

# Por MACRO-setor (fallback antes do DEFAULT) — cobre dezenas de slugs de uma vez.
MACRO_RISK_MESSAGES: Dict[str, Dict[str, str]] = {
    "saude": {"data_risk": "Dados de saúde dos seus pacientes podem ser interceptados — a LGPD "
              "classifica isso como dado sensível.", "audience": "pacientes", "plural": "clínicas"},
    "alimentacao": {"data_risk": "Dados de clientes que pedem delivery podem ser interceptados.",
                    "audience": "clientes", "plural": "estabelecimentos"},
    "comercio": {"data_risk": "Clientes que compram no seu site podem ter dados de pagamento capturados.",
                 "audience": "clientes", "plural": "lojas"},
    "educacao": {"data_risk": "Dados de alunos — incluindo menores — trafegam sem proteção adequada.",
                 "audience": "alunos", "plural": "instituições"},
    "servicos": {"data_risk": "Informações dos seus clientes e projetos podem ser acessadas por terceiros.",
                 "audience": "clientes", "plural": "empresas"},
    "imoveis": {"data_risk": "Documentos de locação/venda e dados pessoais dos seus clientes ficam expostos.",
                "audience": "clientes", "plural": "imobiliárias"},
    "turismo": {"data_risk": "Hóspedes que fazem reserva no seu site podem ter dados de pagamento expostos.",
                "audience": "hóspedes", "plural": "hotéis"},
    "beleza": {"data_risk": "Dados de agendamento e contato dos seus clientes podem ser expostos.",
               "audience": "clientes", "plural": "estabelecimentos"},
    "automotivo": {"data_risk": "Dados de clientes e orçamentos podem ser interceptados.",
                   "audience": "clientes", "plural": "empresas"},
}

# Por SLUG específico (mais preciso que o macro) — só onde vale a pena.
SECTOR_RISK_MESSAGES: Dict[str, Dict[str, str]] = {
    "hotel": {"data_risk": "Hóspedes que fazem reserva no seu site podem ter dados de pagamento expostos.",
              "audience": "hóspedes", "plural": "hotéis"},
    "juridico": {"data_risk": "Documentos e comunicações de clientes trafegam sem proteção pelo seu site.",
                 "audience": "clientes", "plural": "escritórios"},
    "ecommerce": {"data_risk": "Clientes que compram no seu site podem ter dados de pagamento capturados.",
                  "audience": "clientes", "plural": "lojas"},
    "contabilidade": {"data_risk": "Informações fiscais e contábeis dos seus clientes transitam sem proteção.",
                      "audience": "clientes", "plural": "escritórios"},
    "restaurante": {"data_risk": "Dados de clientes que pedem delivery podem ser interceptados.",
                    "audience": "clientes", "plural": "restaurantes"},
    "imobiliaria": {"data_risk": "Documentos de locação e dados pessoais dos seus clientes ficam expostos.",
                    "audience": "clientes", "plural": "imobiliárias"},
    "consultoria": {"data_risk": "Informações dos seus clientes e projetos podem ser acessadas por terceiros.",
                    "audience": "clientes", "plural": "consultorias"},
    "agencia": {"data_risk": "Dados dos seus clientes e acessos a plataformas podem ser comprometidos.",
                "audience": "clientes", "plural": "agências"},
    "clinica": {"data_risk": "Dados de saúde dos seus pacientes podem ser interceptados — a LGPD "
                "classifica isso como dado sensível.", "audience": "pacientes", "plural": "clínicas"},
}

# Variação SETORIAL da mensagem de um check — só onde o contexto muda a consequência.
# Chave interna = slug real OU macro-setor. Lookup em `build_risk_summary`: slug > macro
# > mensagem-base de `RISK_MESSAGES`.
CHECK_SECTOR_RISK: Dict[str, Dict[str, str]] = {
    "check_01_https": {
        "comercio": "Clientes que compram no seu site enviam dados de pagamento sem criptografia.",
        "ecommerce": "Clientes que compram no seu site enviam dados de pagamento sem criptografia.",
        "saude": "Dados de saúde dos seus pacientes trafegam sem proteção.",
        "turismo": "Dados de reserva e pagamento dos seus hóspedes podem ser capturados.",
        "juridico": "Documentos e dados dos seus clientes trafegam sem criptografia.",
        "educacao": "Dados dos seus alunos trafegam sem criptografia.",
    },
    "check_25_form_security": {
        "comercio": "O formulário de compra do seu site envia dados de pagamento sem criptografia.",
        "saude": "O formulário de agendamento envia dados de saúde dos pacientes sem criptografia.",
        "educacao": "O formulário de matrícula envia dados de alunos sem criptografia.",
    },
    "check_28_hibp": {
        "saude": "Credenciais ligadas à sua clínica aparecem em vazamentos — risco de acesso "
                 "a dados de pacientes.",
    },
    "check_23_dmarc": {
        "juridico": "Golpistas podem enviar e-mails como se fossem do seu escritório aos seus clientes.",
        "contabilidade": "Golpistas podem enviar e-mails como se fossem do seu escritório aos seus clientes.",
        "comercio": "Golpistas podem enviar e-mails como se fossem da sua loja aos seus clientes.",
    },
}


def _macro_of(sector: str) -> str:
    try:
        from discovery.sector_taxonomy import get_macro
        return get_macro((sector or "").strip().lower())
    except Exception:  # noqa: BLE001 - taxonomia é best-effort
        return ""


def sector_risk_info(sector: Optional[str]) -> Dict[str, str]:
    """{data_risk, audience, plural} de um setor: slug > macro > default."""
    s = (sector or "").strip().lower()
    if s in SECTOR_RISK_MESSAGES:
        return SECTOR_RISK_MESSAGES[s]
    macro = _macro_of(s)
    if macro in MACRO_RISK_MESSAGES:
        return MACRO_RISK_MESSAGES[macro]
    return DEFAULT_RISK


def build_risk_summary(report_or_results: Any, sector: Optional[str] = None,
                       limit: int = 3) -> Dict[str, Any]:
    """KL-20 — resumo de riscos **setorizado** para e-mail/boletim/dashboard/PDF.

    Retorna ``{"risks": [{check_id, message, severity, headline, icon}],
    "remaining_count": int, "sector_context": str, "audience": str, "plural": str}``.
    A `message` de cada risco usa a variação setorial (`CHECK_SECTOR_RISK`) quando existe
    (slug > macro), senão a mensagem-base de `RISK_MESSAGES`. Sem FAILs → `risks` vazio.
    """
    results = _extract_results(report_or_results)
    fails = [r for r in results if _get(r, "status") == "FAIL" and RISK_MESSAGES.get(_get(r, "check_id"))]
    fails.sort(key=lambda r: _SEV_ORDER.get(_get(r, "severity"), 99))
    slug = (sector or "").strip().lower()
    macro = _macro_of(slug)
    risks: List[Dict[str, str]] = []
    for r in fails[:limit]:
        cid = _get(r, "check_id")
        base = RISK_MESSAGES[cid]
        overrides = CHECK_SECTOR_RISK.get(cid, {})
        message = overrides.get(slug) or overrides.get(macro) or base["risk"]
        risks.append({"check_id": cid, "message": message, "severity": _get(r, "severity"),
                      "headline": base["headline"], "icon": base["icon"]})
    info = sector_risk_info(sector)
    return {"risks": risks, "remaining_count": max(0, len(fails) - len(risks)),
            "sector_context": info["data_risk"], "audience": info["audience"],
            "plural": info["plural"]}


def build_benchmark_line(score: Any, sector: Optional[str] = None,
                         benchmark: Optional[Dict[str, Any]] = None) -> str:
    """KL-20 — linha de benchmark comparativo (usa o `sector_benchmark` do KL-74/P5).

    `benchmark` é o dict de `store.sector_benchmark` (ou None) — função **pura**, o
    chamador busca o benchmark. Score 100 → mensagem de destaque; sem benchmark → só o
    score; acima/abaixo da média → comparação com o setor.
    """
    score = int(score or 0)
    if score >= 100:
        return "Score: 100/100 — nota máxima! Seu site está entre os melhores do Brasil."
    if not benchmark or benchmark.get("avg_score") is None:
        return f"Score: {score}/100"
    avg = int(benchmark.get("avg_score") or 0)
    count = int(benchmark.get("count") or 0)
    plural = sector_risk_info(sector)["plural"]
    if score >= avg:
        tail = f" Com base em {count} {plural}." if count else ""
        return f"Score: {score}/100 — acima da média do setor ({avg})." + tail
    return f"Score: {score}/100 — abaixo da média do setor ({avg}). Há espaço para melhorar."
