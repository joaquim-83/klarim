// KL-135 — banner de consentimento de cookies (LGPD) + carregamento CONDICIONAL do GA4.
// Externo (public/) → passa na CSP `script-src 'self'` sem hash. O GA4 (gtag.js) só é injetado
// se o consentimento incluir analytics — NUNCA carrega sem opt-in.
(function () {
  var GA_ID = 'G-7WPZN66JTB';
  var COOKIE = 'klarim_consent';
  var MAX_AGE = 31536000; // 1 ano

  function getCookie(name) {
    var parts = ('; ' + (document.cookie || '')).split('; ' + name + '=');
    return parts.length === 2 ? decodeURIComponent(parts.pop().split(';').shift()) : '';
  }
  function setCookie(name, value) {
    var secure = (window.location && window.location.protocol === 'https:') ? '; Secure' : '';
    document.cookie = name + '=' + encodeURIComponent(value) +
      '; Max-Age=' + MAX_AGE + '; Path=/; SameSite=Lax' + secure;
  }

  // Injeta o GA4 sob demanda (só com consentimento). Idempotente.
  function loadGA4() {
    if (window.__klarimGA4 || document.querySelector('script[src*="googletagmanager"]')) return;
    window.__klarimGA4 = true;
    var s = document.createElement('script');
    s.async = true;
    s.src = 'https://www.googletagmanager.com/gtag/js?id=' + GA_ID;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    function gtag() { window.dataLayer.push(arguments); }
    window.gtag = gtag;
    gtag('js', new Date());
    gtag('config', GA_ID);
  }

  function el(id) { return document.getElementById(id); }
  function show() { var b = el('cookie-banner'); if (b) { b.removeAttribute('hidden'); b.setAttribute('data-open', '1'); } }
  function hide() { var b = el('cookie-banner'); if (b) { b.removeAttribute('data-open'); b.setAttribute('hidden', ''); } }
  function config(open) {
    var p = el('cc-config');
    if (!p) return;
    if (open === undefined) open = p.hasAttribute('hidden');
    if (open) p.removeAttribute('hidden'); else p.setAttribute('hidden', '');
  }

  // Aplica a escolha: grava o cookie, (des)liga o GA4 e fecha o banner.
  function apply(value) {
    setCookie(COOKIE, value);
    if (value === 'all' || value === 'analytics') loadGA4();
    hide();
  }

  function init() {
    var c = getCookie(COOKIE);
    if (c === 'all' || c === 'analytics') loadGA4();          // já consentiu analytics
    else if (c !== 'essential') show();                       // sem cookie → 1ª visita → banner
    // c === 'essential' → banner oculto, GA4 NÃO carrega
  }

  document.addEventListener('click', function (e) {
    var t = e.target && e.target.closest ? e.target.closest('[data-cc]') : null;
    if (!t) return;
    var a = t.getAttribute('data-cc');
    if (a === 'accept') apply('all');
    else if (a === 'reject') apply('essential');
    else if (a === 'configure') config();
    else if (a === 'save') { var an = el('cc-analytics'); apply(an && an.checked ? 'analytics' : 'essential'); }
    else if (a === 'reopen') { if (e.preventDefault) e.preventDefault(); config(false); show(); }
  });

  // Reabrir o banner (link do footer "Preferências de cookies").
  window.klarimReopenConsent = function () { config(false); show(); };

  init();
})();
