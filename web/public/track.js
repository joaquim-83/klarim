// Tracking interno da jornada do lead (KL-21) — plataforma Astro. Asset EXTERNO (public/):
// passa na CSP `script-src 'self'` sem hash/nonce. Expõe window.klarimTrack.
//
// KL-64 — filtro de tráfego não-humano: os servidores de e-mail (Gmail/Outlook/segurança)
// fazem PRE-FETCH dos links dos alertas com Chrome real (a Cloudflare não os marca como bot) e
// inflavam os visitantes (4.221 "hoje" vs ~200 reais). Agora o tracker NÃO dispara page_view no
// load — ESPERA sinal de interação humana (scroll/click/mouse/touch/tecla) OU 5s com a aba
// visível. Pre-fetches saem em < 1s sem interagir → nunca contados. Eventos de AÇÃO
// (scan_started, account_created, …) disparam na hora, mas carregam `verified_human` (o backend
// filtra por is_human). Continua privacy-first: sem cookies de tracking, session id por visita.
(function () {
  var SID_KEY = 'klarim_sid';
  var UTM_KEY = 'klarim_utm';

  // Eventos PASSIVOS (disparados no load): só contam humano verificado → adiados até o sinal.
  var PASSIVE = { page_view: 1, profile_view: 1, ranking_viewed: 1 };
  var HUMAN_SIGNALS = ['scroll', 'click', 'mousemove', 'touchstart', 'keydown'];

  var humanVerified = false;
  var pending = [];        // eventos passivos aguardando o sinal humano

  function sessionId() {
    try {
      var sid = sessionStorage.getItem(SID_KEY);
      if (!sid) {
        sid = (crypto.randomUUID && crypto.randomUUID()) ||
          String(Date.now()) + Math.random().toString(36).slice(2);
        sessionStorage.setItem(SID_KEY, sid);
      }
      return sid;
    } catch (e) { return 'nostorage'; }
  }

  function utm() {
    var empty = { utm_source: null, utm_medium: null, utm_campaign: null, utm_content: null };
    try {
      var p = new URLSearchParams(window.location.search);
      var fromUrl = {
        utm_source: p.get('utm_source'), utm_medium: p.get('utm_medium'),
        utm_campaign: p.get('utm_campaign'), utm_content: p.get('utm_content'),
      };
      if (fromUrl.utm_source) {
        sessionStorage.setItem(UTM_KEY, JSON.stringify(fromUrl));
        return fromUrl;
      }
      var stored = sessionStorage.getItem(UTM_KEY);
      return stored ? JSON.parse(stored) : empty;
    } catch (e) { return empty; }
  }

  function send(eventType, metadata, targetUrl, detection) {
    try {
      var u = utm();
      var meta = metadata || {};
      if (detection) meta = Object.assign({ detection: detection }, meta);
      fetch('/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        keepalive: true,
        body: JSON.stringify(Object.assign({
          event_type: eventType,
          session_id: sessionId(),
          page_url: window.location.pathname + window.location.search,
          referrer: document.referrer || null,
          target_url: targetUrl || null,
          verified_human: humanVerified,
          metadata: meta,
        }, u)),
      }).catch(function () {});
    } catch (e) { /* tracking nunca quebra a app */ }
  }

  // API pública: eventos de AÇÃO disparam na hora; passivos só depois do sinal humano.
  function track(eventType, metadata, targetUrl) {
    if (PASSIVE[eventType] && !humanVerified) {
      pending.push([eventType, metadata, targetUrl]);
      return;
    }
    send(eventType, metadata, targetUrl);
  }
  window.klarimTrack = track;

  function onHuman(detection) {
    if (humanVerified) return;
    humanVerified = true;
    HUMAN_SIGNALS.forEach(function (e) { document.removeEventListener(e, onSignal, true); });
    // drena os passivos adiados, marcando como humano verificado.
    var q = pending; pending = [];
    q.forEach(function (ev) { send(ev[0], ev[1], ev[2], detection); });
  }
  function onSignal() { onHuman('interaction'); }

  HUMAN_SIGNALS.forEach(function (e) {
    document.addEventListener(e, onSignal, { once: true, passive: true, capture: true });
  });
  // Fallback: 5s com a aba visível = humano lendo (pre-fetch de e-mail não fica 5s visível).
  setTimeout(function () {
    if (!humanVerified && document.visibilityState === 'visible') onHuman('timeout');
  }, 5000);

  // Eventos passivos do load (adiados até o sinal). O painel/dashboard ficam de fora.
  var path = window.location.pathname;
  if (path.indexOf('/painel') !== 0 && path.indexOf('/dashboard') !== 0) {
    track('page_view');
    // profile_view em /site/{dominio}: mede tráfego dos perfis E dispara o aviso ao dono
    // (só humano — KL-64). O e-mail nasce do evento humano-verificado no backend, não do SSR.
    var m = path.match(/^\/site\/([^/]+)/);
    if (m) {
      var dom = decodeURIComponent(m[1]);
      track('profile_view', { domain: dom }, 'https://' + dom);
    }
    var r = path.match(/^\/ranking(?:\/([^/]+))?/);
    if (r) track('ranking_viewed', { sector: r[1] ? decodeURIComponent(r[1]) : null });
  }
})();
