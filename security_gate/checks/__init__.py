"""Security Gate — checks. KL-141: exposição/credenciais (novos) + headers/SSL (reusam o
`scanner/`) + API security. KL-149: +13 checks (CORS, cookies, redirects, rate limit, error
disclosure, JWT, forms, DNS, dependencies, TLS ciphers, subdomain takeover, infra URLs)."""
from __future__ import annotations

from .api_security import check_api_security
from .cookies import check_cookies
from .cors import check_cors
from .credentials import check_credentials
from .dependencies import check_dependencies
from .dns_security import check_dns_security
from .error_disclosure import check_error_disclosure
from .exposure import check_exposure
from .form_security import check_form_security
from .headers import check_headers
from .https_redirect import check_https_redirect
from .infrastructure_urls import check_infrastructure_urls
from .jwt_analysis import check_jwt
from .rate_limit import check_rate_limit
from .redirect import check_open_redirect
from .ssl import check_ssl
from .subdomain import check_subdomain_takeover
from .tls_ciphers import check_tls_ciphers

__all__ = [
    "check_api_security", "check_credentials", "check_exposure", "check_headers", "check_ssl",
    "check_cors", "check_cookies", "check_https_redirect", "check_open_redirect",
    "check_rate_limit", "check_error_disclosure", "check_jwt", "check_form_security",
    "check_dns_security", "check_dependencies", "check_tls_ciphers", "check_subdomain_takeover",
    "check_infrastructure_urls",
]
