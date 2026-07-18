"""Geração de relatórios PDF (executivo + técnico) a partir de um ScanReport.

Fluxo: ``ScanReport`` -> contexto -> template Jinja2 (HTML) -> WeasyPrint -> PDF.

Duas funções públicas:

* :func:`generate_executive_pdf` — 1-2 páginas, para o dono do negócio.
* :func:`generate_technical_pdf` — 3-5 páginas, para dev/agência.

Ambas são ``async``: a renderização (CPU-bound) roda em thread separada via
``asyncio.to_thread`` para não bloquear o event loop da API.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape
from weasyprint import HTML

from scanner import __version__ as scanner_version
from scanner.runner import ScanReport
from scanner.checks.base import Status, Severity
from scanner.checks.classifications import classify, LGPD_LABELS, compliance_summary
from .risk_messages import get_risk_messages, get_risk_summary

_HERE = Path(__file__).resolve().parent
_TEMPLATES = _HERE / "templates"
_ASSETS = _HERE / "assets"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


# --------------------------------------------------------------------------- #
# Paleta e rótulos
# --------------------------------------------------------------------------- #

COLORS = {
    "bg": "#0D1117",
    "text": "#E6EDF3",
    "alert": "#FF6B35",   # laranja — alarme
    "ok": "#00D26A",      # verde — segurança
    "muted": "#8B949E",
    "panel": "#161B22",
    "border": "#30363D",
}

# Cor do semáforo por faixa de score.
SEMAPHORE_COLOR = {"verde": "#00D26A", "amarelo": "#F2C744", "vermelho": "#FF4D4D"}
SEMAPHORE_LABEL = {"verde": "VERDE", "amarelo": "AMARELO", "vermelho": "VERMELHO"}

SEVERITY_LABEL = {
    Severity.CRITICA: "Crítica",
    Severity.ALTA: "Alta",
    Severity.MEDIA: "Média",
    Severity.BAIXA: "Baixa",
}
SEVERITY_COLOR = {
    Severity.CRITICA: "#FF4D4D",
    Severity.ALTA: "#FF6B35",
    Severity.MEDIA: "#F2C744",
    Severity.BAIXA: "#58A6FF",
}

STATUS_LABEL = {Status.PASS: "PASS", Status.FAIL: "FAIL", Status.INCONCLUSO: "INCONCLUSO"}
STATUS_COLOR = {Status.PASS: "#00D26A", Status.FAIL: "#FF4D4D", Status.INCONCLUSO: "#F2C744"}


# --------------------------------------------------------------------------- #
# Traduções acessíveis (executivo) — o que significa cada FALHA em linguagem
# de negócio, sem jargão.
# --------------------------------------------------------------------------- #

ACCESSIBLE: Dict[str, str] = {
    "check_01_https": "Seu site não redireciona automaticamente para uma conexão segura (HTTPS).",
    "check_02_hsts": "Seu site não força conexão segura em todas as visitas (HSTS ausente).",
    "check_03_ssl": "O certificado de segurança (SSL) do seu site apresenta problemas.",
    "check_04_tls": "Seu site aceita protocolos de criptografia antigos e inseguros.",
    "check_05_csp": "Seu site não tem proteção contra a injeção de scripts maliciosos.",
    "check_06_xfo": "Seu site pode ser incorporado em páginas falsas para aplicar golpes (clickjacking).",
    "check_07_xcto": "O navegador pode interpretar arquivos do seu site de forma insegura.",
    "check_08_server": "Seu site revela a versão do software do servidor, o que facilita ataques.",
    "check_09_sourcemaps": "O código-fonte do seu site está exposto publicamente.",
    "check_10_sensitive": "Arquivos sensíveis do seu site estão acessíveis publicamente.",
    "check_11_dirlist": "As pastas do seu site permitem que qualquer um liste todos os arquivos.",
    "check_12_metatags": "Seu site expõe qual tecnologia foi usada para construí-lo.",
    "check_13_sri": "Scripts de terceiros são carregados sem verificação de integridade.",
    "check_14_risky_sources": "Seu site carrega código de fontes não confiáveis.",
    "check_15_external_domains": "Seu site carrega scripts de um número elevado de domínios externos.",
    "check_16_api_docs": "A documentação técnica da sua API está acessível publicamente.",
    "check_17_cookies": "Cookies de sessão do seu site não têm todas as flags de segurança.",
    "check_18_cors": "Sua API aceita requisições de qualquer site (CORS permissivo).",
    "check_19_redirect_domain": "Seu site redireciona para um domínio diferente do original.",
    "check_20_info_disclosure": "Seu servidor confirma a existência de arquivos internos ao bloqueá-los.",
    "check_21_spf": "Seu domínio não protege contra o envio de e-mails falsos em seu nome (SPF).",
    "check_22_dkim": "Os e-mails do seu domínio não têm assinatura digital (DKIM).",
    "check_23_dmarc": "Seu domínio não tem política de proteção contra phishing (DMARC).",
    "check_24_mixed_content": "Seu site seguro (HTTPS) carrega arquivos por conexão insegura (HTTP).",
    "check_25_form_security": "Formulários do seu site enviam dados de forma insegura.",
    "check_26_subdomains": "Ambientes internos (staging/admin/API) estão expostos publicamente.",
    "check_27_dangling_cname": "Um subdomínio aponta para um serviço inexistente (risco de sequestro).",
    "check_28_hibp": "O domínio da sua empresa aparece em vazamentos de dados conhecidos.",
    "check_29_safe_browsing": "O Google marcou seu site como perigoso (malware/phishing).",
    "check_30_vulnerable_components": "Seu site usa versões antigas de ferramentas com falhas de segurança já conhecidas e catalogadas.",
    "check_31_permissions_policy": "Seu site não restringe o acesso de scripts à câmera, microfone e localização do visitante.",
    "check_32_coop": "Seu site não tem uma proteção moderna contra ataques que abrem seu site em outra janela.",
    "check_33_coep": "Seu site não isola recursos carregados de outros sites (proteção moderna do navegador).",
    "check_34_corp": "Seu site não controla quais outros sites podem carregar os seus recursos.",
    "check_35_referrer_policy": "Ao clicar em links, seu site pode enviar o endereço completo (com dados) para outros sites.",
    "check_36_cache_control_forms": "Páginas com formulários do seu site podem ficar guardadas no cache do navegador.",
    "check_37_dnssec": "As respostas de DNS do seu domínio não são autenticadas — alguém poderia redirecionar seus visitantes para uma cópia falsa do site.",
    "check_38_caa": "Seu domínio não define quais empresas podem emitir certificados de segurança para ele — qualquer uma pode.",
    "check_39_mta_sts": "Os e-mails enviados para o seu domínio podem ser interceptados no caminho, sem criptografia obrigatória.",
    "check_40_bimi": "Seu domínio não exibe o logo da empresa nos e-mails — os clientes veem um ícone genérico em vez da sua marca.",
    "check_41_cipher_suites": "A criptografia do seu site usa um algoritmo fraco, que já foi quebrado.",
    "check_42_cert_chain": "A cadeia do certificado de segurança do seu site tem um problema de configuração.",
    "check_43_ocsp_stapling": "Seu site não verifica automaticamente a validade do certificado a cada conexão.",
    "check_44_key_strength": "A chave de criptografia do seu site é fraca demais para os padrões atuais.",
    "check_45_html_comments": "O código do seu site tem comentários que revelam informações internas (servidores, caminhos de arquivos).",
    "check_46_debug_mode": "Seu site mostra informações técnicas internas quando ocorre um erro.",
    "check_47_open_redirect": "Seu site tem links que podem redirecionar visitantes para sites maliciosos.",
    "check_48_password_fields": "O campo de senha do seu site permite que o navegador salve a senha automaticamente.",
}


# --------------------------------------------------------------------------- #
# Conteúdo técnico — impacto + recomendação de correção (com exemplo).
# --------------------------------------------------------------------------- #

TECHNICAL: Dict[str, Dict[str, str]] = {
    "check_01_https": {
        "impact": "Tráfego servido em HTTP pode ser interceptado ou alterado por um atacante na rede (man-in-the-middle).",
        "fix": "Redirecione todo HTTP (porta 80) para HTTPS com 301 e sirva o site exclusivamente por TLS.",
        "fix_code": "# nginx\nserver {\n    listen 80;\n    return 301 https://$host$request_uri;\n}",
    },
    "check_02_hsts": {
        "impact": "Sem HSTS, o primeiro acesso pode ser forçado a HTTP e sofrer downgrade/interceptação.",
        "fix": "Envie o header HSTS em todas as respostas HTTPS.",
        "fix_code": "Strict-Transport-Security: max-age=31536000; includeSubDomains",
    },
    "check_03_ssl": {
        "impact": "Certificado expirado, não confiável ou com domínio incorreto quebra a confiança e permite MITM.",
        "fix": "Emita/renove o certificado por uma CA confiável (ex.: Let's Encrypt) cobrindo todos os domínios usados.",
        "fix_code": "certbot --nginx -d exemplo.com.br -d www.exemplo.com.br",
    },
    "check_04_tls": {
        "impact": "TLS 1.0/1.1 têm vulnerabilidades conhecidas (BEAST, POODLE) e não devem ser aceitos.",
        "fix": "Aceite apenas TLS 1.2+.",
        "fix_code": "# nginx\nssl_protocols TLSv1.2 TLSv1.3;",
    },
    "check_05_csp": {
        "impact": "Sem CSP, uma falha de XSS pode executar scripts arbitrários no contexto do site.",
        "fix": "Defina uma Content-Security-Policy restritiva e vá ajustando as origens confiáveis.",
        "fix_code": "Content-Security-Policy: default-src 'self'; script-src 'self' cdn.exemplo.com",
    },
    "check_06_xfo": {
        "impact": "Sem proteção, a página pode ser embutida em um iframe invisível para clickjacking.",
        "fix": "Envie X-Frame-Options (ou a diretiva CSP frame-ancestors).",
        "fix_code": "X-Frame-Options: DENY\n# ou\nContent-Security-Policy: frame-ancestors 'none'",
    },
    "check_07_xcto": {
        "impact": "Sem nosniff, o navegador pode adivinhar o tipo de um arquivo e executá-lo como algo perigoso.",
        "fix": "Envie o header X-Content-Type-Options.",
        "fix_code": "X-Content-Type-Options: nosniff",
    },
    "check_08_server": {
        "impact": "Expor a versão exata do servidor facilita ao atacante buscar exploits conhecidos.",
        "fix": "Oculte a versão do servidor e do stack.",
        "fix_code": "# nginx\nserver_tokens off;",
    },
    "check_09_sourcemaps": {
        "impact": "Source maps (.js.map) revelam o código-fonte original, comentários e lógica interna da aplicação.",
        "fix": "Não publique arquivos .map em produção; desative a geração de source maps no build.",
        "fix_code": "# Create React App\nGENERATE_SOURCEMAP=false npm run build",
    },
    "check_10_sensitive": {
        "impact": "Arquivos como .env, .git/config e backups podem conter segredos, credenciais e chaves.",
        "fix": "Remova esses arquivos do webroot e bloqueie o acesso a caminhos sensíveis.",
        "fix_code": "# nginx\nlocation ~ /\\.(env|git) { deny all; return 404; }",
    },
    "check_11_dirlist": {
        "impact": "A listagem de diretório expõe todos os arquivos de uma pasta, inclusive os não intencionais.",
        "fix": "Desative o autoindex do servidor.",
        "fix_code": "# nginx\nautoindex off;",
    },
    "check_12_metatags": {
        "impact": "Fingerprints de framework em meta tags ajudam o atacante a escolher exploits específicos.",
        "fix": "Remova as meta tags default do framework na build de produção.",
        "fix_code": "<!-- remova: <meta name=\"generator\" content=\"...\"> -->",
    },
    "check_13_sri": {
        "impact": "Se um CDN de terceiros for comprometido, um script alterado executa sem qualquer detecção.",
        "fix": "Adicione Subresource Integrity (SRI) e crossorigin a cada script externo.",
        "fix_code": "<script src=\"https://cdn.exemplo.com/lib.js\"\n        integrity=\"sha384-...\"\n        crossorigin=\"anonymous\"></script>",
    },
    "check_14_risky_sources": {
        "impact": "Código servido de GitHub Pages pessoal, buckets S3 públicos ou paste sites pode ser alterado por terceiros a qualquer momento — vetor direto de supply chain.",
        "fix": "Hospede o script em infraestrutura própria ou CDN confiável e aplique SRI.",
        "fix_code": "<!-- evite: https://usuario.github.io/... , https://bucket.s3.amazonaws.com/... -->",
    },
    "check_15_external_domains": {
        "impact": "Cada domínio externo que carrega script é mais um elo na cadeia de suprimentos e mais uma superfície de ataque.",
        "fix": "Reduza dependências de terceiros, consolide provedores e audite periodicamente os scripts carregados.",
        "fix_code": "",
    },
    "check_16_api_docs": {
        "impact": "Documentação (Swagger/OpenAPI/GraphQL) exposta mapeia todos os endpoints e parâmetros para um atacante.",
        "fix": "Desabilite a documentação em produção.",
        "fix_code": "# FastAPI\napp = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)",
    },
    "check_17_cookies": {
        "impact": "Cookies de sessão sem Secure/HttpOnly/SameSite podem ser roubados via XSS ou enviados por HTTP.",
        "fix": "Adicione as flags de segurança aos cookies de sessão.",
        "fix_code": "Set-Cookie: session=...; Secure; HttpOnly; SameSite=Strict",
    },
    "check_18_cors": {
        "impact": "Access-Control-Allow-Origin: * permite que qualquer site faça requisições autenticadas à API.",
        "fix": "Restrinja o CORS a origens confiáveis.",
        "fix_code": "Access-Control-Allow-Origin: https://seusite.com.br",
    },
    "check_19_redirect_domain": {
        "impact": "Redirect para outro domínio: se o domínio original expirar, pode ser registrado e usado para se passar por você.",
        "fix": "Mantenha o site no mesmo domínio, ou proteja ambos com HTTPS e renovação automática.",
        "fix_code": "",
    },
    "check_20_info_disclosure": {
        "impact": "Um 403 (em vez de 404) confirma que o arquivo existe no servidor — information disclosure.",
        "fix": "Retorne 404 para caminhos internos, sem revelar a existência.",
        "fix_code": "# nginx\nlocation ~ /\\.(git|env) { return 404; }",
    },
    "check_21_spf": {
        "impact": "Sem SPF restritivo, qualquer servidor pode enviar e-mails falsos em nome do domínio (spoofing).",
        "fix": "Publique um registro SPF (TXT) apontando o(s) provedor(es) de e-mail e terminando em ~all/-all.",
        "fix_code": "v=spf1 include:_spf.google.com ~all",
    },
    "check_22_dkim": {
        "impact": "Sem DKIM, os e-mails do domínio não têm assinatura criptográfica e são mais facilmente marcados como spam.",
        "fix": "Ative o DKIM no provedor de e-mail e publique o registro TXT do seletor no DNS.",
        "fix_code": "selector._domainkey  IN TXT  \"v=DKIM1; k=rsa; p=...\"",
    },
    "check_23_dmarc": {
        "impact": "Sem DMARC (ou com p=none), não há bloqueio de e-mails falsificados — só monitoramento.",
        "fix": "Publique um registro DMARC com política quarantine ou reject.",
        "fix_code": "_dmarc  IN TXT  \"v=DMARC1; p=quarantine; rua=mailto:dmarc@seudominio.com.br\"",
    },
    "check_24_mixed_content": {
        "impact": "Recursos carregados via HTTP em uma página HTTPS podem ser interceptados e substituídos por código malicioso.",
        "fix": "Atualize todas as referências de recursos para HTTPS.",
        "fix_code": "<script src=\"https://cdn.exemplo.com/lib.js\"></script>",
    },
    "check_25_form_security": {
        "impact": "Formulários com action HTTP (ou cross-origin) enviam dados dos usuários sem criptografia.",
        "fix": "Envie todos os formulários por HTTPS, para o mesmo domínio.",
        "fix_code": "<form action=\"https://seusite.com.br/enviar\" method=\"post\">",
    },
    "check_26_subdomains": {
        "impact": "Subdomínios de teste/admin/API expostos ampliam a superfície de ataque — costumam ter menos proteção.",
        "fix": "Revise os subdomínios expostos e remova certificados de ambientes que não precisam ser públicos.",
        "fix_code": "",
    },
    "check_27_dangling_cname": {
        "impact": "Um CNAME apontando para um serviço desativado permite que outra pessoa registre o serviço e assuma o subdomínio (takeover).",
        "fix": "Remova o registro CNAME órfão ou reative o serviço de destino.",
        "fix_code": "# remova o CNAME de blog.seusite.com.br -> servico-morto.herokuapp.com",
    },
    "check_28_hibp": {
        "impact": "O domínio aparece em vazamentos conhecidos — credenciais de clientes/funcionários podem estar circulando.",
        "fix": "Verifique quais contas foram comprometidas e force a troca de senhas dos afetados.",
        "fix_code": "",
    },
    "check_29_safe_browsing": {
        "impact": "O Google flagou o site como malware/phishing — navegadores mostram alerta vermelho antes de acessar.",
        "fix": "Remova o conteúdo malicioso e solicite revisão no Google Search Console.",
        "fix_code": "# https://search.google.com/search-console/security-issues",
    },
    "check_30_vulnerable_components": {
        "impact": "Componentes desatualizados (bibliotecas JS, CMS) com CVEs públicos entregam ao atacante um roteiro pronto de exploração — o exploit já existe, é documentado e muitas vezes automatizado.",
        "fix": "Atualize as bibliotecas e o CMS para versões suportadas; automatize a verificação de dependências (Dependabot, npm audit, Retire.js).",
        "fix_code": "<!-- ex.: jQuery -->\n<script src=\"https://code.jquery.com/jquery-3.7.1.min.js\"></script>\n# WordPress: Painel → Atualizações → atualizar o core e os plugins",
    },
    "check_31_permissions_policy": {
        "impact": "Sem Permissions-Policy, qualquer script (inclusive de terceiros) pode acessar câmera, microfone, geolocalização e pagamento sem restrição adicional.",
        "fix": "Declare uma Permissions-Policy negando por default e liberando só o necessário.",
        "fix_code": "Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=()",
    },
    "check_32_coop": {
        "impact": "Sem COOP, uma janela aberta pelo site (ou que abriu o site) mantém referência via window.opener e pode ser alvo de ataques cross-origin (tabnabbing, XS-Leaks).",
        "fix": "Envie Cross-Origin-Opener-Policy com same-origin.",
        "fix_code": "Cross-Origin-Opener-Policy: same-origin",
    },
    "check_33_coep": {
        "impact": "Sem COEP, recursos cross-origin são embutidos sem opt-in; impede também habilitar isolamento (necessário para APIs sensíveis do navegador).",
        "fix": "Envie Cross-Origin-Embedder-Policy com require-corp (ou credentialless).",
        "fix_code": "Cross-Origin-Embedder-Policy: require-corp",
    },
    "check_34_corp": {
        "impact": "Sem CORP, os recursos do site podem ser carregados por qualquer origem, facilitando ataques de canal lateral (Spectre/XS-Leaks).",
        "fix": "Envie Cross-Origin-Resource-Policy conforme o uso (same-origin para recursos privados).",
        "fix_code": "Cross-Origin-Resource-Policy: same-origin",
    },
    "check_35_referrer_policy": {
        "impact": "Com 'unsafe-url' (ou sem policy), a URL completa — incluindo query strings com dados sensíveis — é enviada a sites de terceiros no header Referer.",
        "fix": "Declare Referrer-Policy com strict-origin-when-cross-origin.",
        "fix_code": "Referrer-Policy: strict-origin-when-cross-origin",
    },
    "check_36_cache_control_forms": {
        "impact": "Páginas com formulário/senha sem Cache-Control podem ficar no cache do navegador/proxy; em um computador compartilhado a próxima pessoa pode ver os dados.",
        "fix": "Envie Cache-Control: no-store nas páginas com dados sensíveis.",
        "fix_code": "Cache-Control: no-store, max-age=0",
    },
    "check_37_dnssec": {
        "impact": "Sem DNSSEC (registro DS ausente no parent zone), as respostas DNS não são assinadas — o domínio fica vulnerável a cache poisoning/spoofing, redirecionando visitantes a uma cópia falsa.",
        "fix": "Ative DNSSEC no registrar/provedor de DNS (Registro.br, Cloudflare, Hostinger).",
        "fix_code": "# Registro.br / painel do provedor: ativar DNSSEC (publica DS no parent zone)",
    },
    "check_38_caa": {
        "impact": "Sem CAA, qualquer autoridade certificadora pode emitir um certificado válido para o domínio, facilitando MitM com certificado fraudulento (RFC 8659).",
        "fix": "Publique um registro CAA restringindo às CAs que você usa.",
        "fix_code": "exemplo.com.br.  IN CAA 0 issue \"letsencrypt.org\"\nexemplo.com.br.  IN CAA 0 iodef \"mailto:seguranca@exemplo.com.br\"",
    },
    "check_39_mta_sts": {
        "impact": "Sem MTA-STS, e-mails de entrada podem sofrer downgrade de TLS (STARTTLS stripping) e ser lidos em trânsito (RFC 8461).",
        "fix": "Publique o TXT _mta-sts e a policy em mta-sts.<domínio>/.well-known/mta-sts.txt com mode: enforce.",
        "fix_code": "_mta-sts.exemplo.com.br. IN TXT \"v=STSv1; id=20260101000000\"\n# policy:\nversion: STSv1\nmode: enforce\nmx: mail.exemplo.com.br\nmax_age: 604800",
    },
    "check_40_bimi": {
        "impact": "Sem BIMI, o logo da marca não aparece nos e-mails (Gmail/Apple Mail) — reduz a confiança e dificulta distinguir e-mails legítimos de falsos. Requer DMARC em enforce.",
        "fix": "Configure DMARC com p=quarantine/reject e publique o TXT BIMI apontando o logo (SVG).",
        "fix_code": "default._bimi.exemplo.com.br. IN TXT \"v=BIMI1; l=https://exemplo.com.br/logo.svg\"",
    },
    "check_41_cipher_suites": {
        "impact": "Cipher fraco (RC4/DES/3DES) ou sem forward secrecy permite decifrar o tráfego (agora ou no futuro, se a chave vazar). RC4 está quebrado desde 2015 (RFC 7465).",
        "fix": "Desabilite ciphers legados e priorize suites ECDHE + AES-GCM; habilite TLS 1.3.",
        "fix_code": "# nginx\nssl_protocols TLSv1.2 TLSv1.3;\nssl_ciphers ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;\nssl_prefer_server_ciphers on;",
    },
    "check_42_cert_chain": {
        "impact": "Certificado self-signed ou cadeia incompleta faz o navegador rejeitar (ou completar de forma frágil) a conexão; expiração próxima leva a downtime de confiança.",
        "fix": "Sirva leaf + intermediários (fullchain) de uma CA confiável e automatize a renovação.",
        "fix_code": "# nginx\nssl_certificate     /etc/letsencrypt/live/dominio/fullchain.pem;\nssl_certificate_key /etc/letsencrypt/live/dominio/privkey.pem;",
    },
    "check_43_ocsp_stapling": {
        "impact": "Sem OCSP, a verificação de revogação do certificado fica lenta e vaza a navegação do usuário para a CA; sem OCSP URI, a revogação nem é consultável.",
        "fix": "Use uma CA com OCSP e habilite OCSP stapling no servidor.",
        "fix_code": "# nginx\nssl_stapling on;\nssl_stapling_verify on;\nresolver 1.1.1.1 valid=300s;",
    },
    "check_44_key_strength": {
        "impact": "Chave RSA de 1024 bits é quebrável por fatoração (NIST deprecou em 2013, proibiu em 2014) — permite forjar o certificado e fazer MitM.",
        "fix": "Gere uma nova chave RSA 2048+ (ideal 4096) ou migre para ECDSA P-256 e reemita o certificado.",
        "fix_code": "openssl ecparam -genkey -name prime256v1 -out key.pem   # ECDSA P-256\n# ou: openssl genrsa -out key.pem 4096",
    },
    "check_45_html_comments": {
        "impact": "Comentários HTML com nomes de servidores internos, IPs, paths de sistema ou TODOs de segurança dão ao atacante um mapa da infraestrutura e das fraquezas conhecidas.",
        "fix": "Remova todos os comentários HTML com informação operacional na build de produção (minificação normalmente os elimina).",
        "fix_code": "<!-- remova: server: db-prod-01.internal / TODO: fix XSS / /var/www/config.php -->",
    },
    "check_46_debug_mode": {
        "impact": "Debug em produção expõe stack traces com paths de arquivos, versões de framework e trechos de código/SQL — roteiro pronto para explorar a aplicação.",
        "fix": "Desative o modo debug em produção e configure páginas de erro genéricas.",
        "fix_code": "# Django: DEBUG=False\n# Laravel: APP_DEBUG=false\n# WordPress: define('WP_DEBUG', false);\n# PHP: display_errors=Off",
    },
    "check_47_open_redirect": {
        "impact": "Parâmetros de redirect sem validação permitem que um link com o seu domínio leve a uma página de phishing (o usuário confia porque começa no seu site).",
        "fix": "Valide o destino do redirect contra uma whitelist de domínios/paths do próprio site.",
        "fix_code": "# só aceitar caminhos relativos internos:\nif not next_url.startswith('/') or next_url.startswith('//'):\n    next_url = '/'",
    },
    "check_48_password_fields": {
        "impact": "Campo de senha sem autocomplete adequado permite que o navegador salve/autocomplete a senha — risco em computadores compartilhados (LGPD Art. 11, dados sensíveis).",
        "fix": "Use autocomplete='new-password' no cadastro/troca e 'current-password' no login; garanta HTTPS e Cache-Control: no-store.",
        "fix_code": "<input type=\"password\" name=\"pass\" autocomplete=\"new-password\">",
    },
}

LGPD_TEXT = (
    "Se o seu site coleta dados pessoais (nome, CPF, e-mail, cartão de crédito), "
    "você está sujeito à Lei Geral de Proteção de Dados (LGPD). Falhas de segurança "
    "podem resultar em sanções de até R$ 50 milhões por infração (Art. 52)."
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def site_name(url: str) -> str:
    host = (urlparse(url).hostname or url).lower()
    return host[4:] if host.startswith("www.") else host


def report_id(url: str, started_at: str) -> str:
    """ID estável por scan (mesmo alvo + mesmo instante -> mesmo ID)."""
    digest = hashlib.sha256(f"{url}|{started_at}".encode()).hexdigest()[:8].upper()
    return f"KLR-{digest}"


def _fmt_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%d/%m/%Y %H:%M UTC")
    except (ValueError, TypeError):
        return iso


def _date_for_filename(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return "sem-data"


def _logo_svg() -> str:
    return (_ASSETS / "logo.svg").read_text(encoding="utf-8")


def _collect_headers(report: ScanReport) -> List[Dict[str, str]]:
    """Reúne os headers HTTP notáveis capturados pelos checks de header."""
    by_id = {r.check_id: r for r in report.results}
    out: List[Dict[str, str]] = []

    def add(name: str, value: Optional[str]) -> None:
        if value:
            out.append({"name": name, "value": str(value)})

    if "check_02_hsts" in by_id:
        add("Strict-Transport-Security", by_id["check_02_hsts"].details.get("header"))
    if "check_05_csp" in by_id:
        add("Content-Security-Policy", by_id["check_05_csp"].details.get("header"))
    if "check_06_xfo" in by_id:
        d = by_id["check_06_xfo"].details
        add("X-Frame-Options", d.get("header"))
    if "check_07_xcto" in by_id:
        add("X-Content-Type-Options", by_id["check_07_xcto"].details.get("header"))
    if "check_08_server" in by_id:
        d = by_id["check_08_server"].details
        add("Server", d.get("server"))
        add("X-Powered-By", d.get("x_powered_by"))
    return out


def _lgpd_display(value: Optional[str]) -> str:
    """``"Art. 46, Art. 48"`` -> rótulos amigáveis unidos (vazio se ``None``)."""
    if not value:
        return ""
    arts = [a.strip() for a in value.split(",") if a.strip()]
    return ", ".join(LGPD_LABELS.get(a, a) for a in arts)


def _build_context(report: ScanReport, target_url: str,
                   sector: Optional[str] = None) -> Dict[str, Any]:
    s = report.score
    sev = s.fails_by_severity if s else {}
    by_id = {r.check_id: r for r in report.results}

    fails = []
    for r in report.results:
        if r.status != Status.FAIL:
            continue
        tech = TECHNICAL.get(r.check_id, {})
        # Classificação de compliance (KL-34/35): usa o carimbo do runner e cai para
        # o mapa por check_id (robusto para reports antigos sem os campos no JSON).
        cls = classify(r.check_id)
        fails.append({
            "check_id": r.check_id,
            "name": r.name,
            "severity": r.severity,
            "severity_label": SEVERITY_LABEL.get(r.severity, r.severity),
            "severity_color": SEVERITY_COLOR.get(r.severity, COLORS["muted"]),
            "evidence": r.evidence,
            "accessible": ACCESSIBLE.get(r.check_id, r.name),
            "impact": tech.get("impact", ""),
            "fix": tech.get("fix", ""),
            "fix_code": tech.get("fix_code", ""),
            # Só o template técnico renderiza estes; o executivo os ignora.
            "owasp": r.owasp or cls.owasp,
            "cwe": r.cwe or cls.cwe,
            "lgpd": _lgpd_display(r.lgpd or cls.lgpd),
        })

    all_checks = []
    for i, r in enumerate(report.results, start=1):
        all_checks.append({
            "num": i,
            "name": r.name,
            "status": r.status,
            "status_label": STATUS_LABEL.get(r.status, r.status),
            "status_color": STATUS_COLOR.get(r.status, COLORS["muted"]),
            "severity_label": SEVERITY_LABEL.get(r.severity, r.severity),
            "severity_color": SEVERITY_COLOR.get(r.severity, COLORS["muted"]),
            "evidence": r.evidence,
        })

    # Inventário (supply chain) a partir dos details dos checks.
    inv_external = by_id.get("check_15_external_domains")
    inv_sri = by_id.get("check_13_sri")
    inv_risky = by_id.get("check_14_risky_sources")

    inventory = {
        "external_domains": (inv_external.details.get("external_domains", []) if inv_external else []),
        "without_sri": (inv_sri.details.get("without_sri_urls", []) if inv_sri else []),
        "risky_scripts": (inv_risky.details.get("risky_scripts", []) if inv_risky else []),
        "headers": _collect_headers(report),
    }

    # KL-20: com o setor, as mensagens de risco ganham a variação setorial (linguagem de
    # negócio específica). Sem setor → mensagens-base (comportamento anterior).
    if sector and sector != "outro":
        from .risk_messages import build_risk_summary
        rs = build_risk_summary(report, sector, limit=5)
        risk_messages = [{"icon": r["icon"], "headline": r["headline"], "risk": r["message"],
                          "check_id": r["check_id"], "severity": r["severity"]}
                         for r in rs["risks"]]
    else:
        risk_messages = get_risk_messages(report)
    risk_summary = get_risk_summary(risk_messages)

    n = s.failed if s else 0
    if n == 0:
        problem_line = "Nenhum problema de segurança foi encontrado no seu site."
    elif n == 1:
        problem_line = "Encontramos 1 problema de segurança no seu site."
    else:
        problem_line = f"Encontramos {n} problemas de segurança no seu site."

    return {
        "colors": COLORS,
        "logo_svg": _logo_svg(),
        "site_name": site_name(target_url),
        "target_url": target_url,
        "scan_date": _fmt_date(report.started_at),
        "report_id": report_id(target_url, report.started_at),
        "scanner_version": scanner_version,
        "score": s.score if s else 0,
        "semaphore": s.semaphore if s else "vermelho",
        "semaphore_label": SEMAPHORE_LABEL.get(s.semaphore if s else "vermelho", ""),
        "score_color": SEMAPHORE_COLOR.get(s.semaphore if s else "vermelho", "#FF4D4D"),
        "counts": {
            "passed": s.passed if s else 0,
            "failed": s.failed if s else 0,
            "inconclusive": s.inconclusive if s else 0,
        },
        "sev_counts": {
            "critica": sev.get(Severity.CRITICA, 0),
            "alta": sev.get(Severity.ALTA, 0),
            "media": sev.get(Severity.MEDIA, 0),
            "baixa": sev.get(Severity.BAIXA, 0),
        },
        "sev_colors": {
            "critica": SEVERITY_COLOR[Severity.CRITICA],
            "alta": SEVERITY_COLOR[Severity.ALTA],
            "media": SEVERITY_COLOR[Severity.MEDIA],
            "baixa": SEVERITY_COLOR[Severity.BAIXA],
        },
        "problem_line": problem_line,
        "n_problems": n,
        "fails": fails,
        "all_checks": all_checks,
        "inventory": inventory,
        "risk_messages": risk_messages,
        "risk_summary": risk_summary,
        "lgpd_text": LGPD_TEXT,
        # Sumário de conformidade (KL-34/35) — só o template técnico o consome.
        "compliance": compliance_summary(report.results),
    }


def _render_pdf(html_str: str) -> bytes:
    return HTML(string=html_str, base_url=str(_HERE)).write_pdf()


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #

async def generate_executive_pdf(scan_report: ScanReport, target_url: str,
                                 sector: Optional[str] = None) -> bytes:
    """Gera o PDF executivo (semáforo) a partir de um ScanReport. `sector` (KL-20) ativa a
    variação setorial das mensagens de risco."""
    ctx = _build_context(scan_report, target_url, sector)
    html_str = _env.get_template("executive.html").render(**ctx)
    return await asyncio.to_thread(_render_pdf, html_str)


async def generate_technical_pdf(scan_report: ScanReport, target_url: str,
                                 sector: Optional[str] = None) -> bytes:
    """Gera o PDF técnico (detalhado) a partir de um ScanReport."""
    ctx = _build_context(scan_report, target_url, sector)
    html_str = _env.get_template("technical.html").render(**ctx)
    return await asyncio.to_thread(_render_pdf, html_str)


def pdf_filename(kind: str, target_url: str, started_at: str) -> str:
    """Nome de arquivo padrão: klarim_<kind>_<host>_<data>.pdf."""
    return f"klarim_{kind}_{site_name(target_url)}_{_date_for_filename(started_at)}.pdf"
