// Tracking interno da jornada do lead (KL-21) para a plataforma Astro.
// Servido como ASSET EXTERNO (public/) — não é script inline, então passa na CSP
// `script-src 'self'` sem precisar de hash/nonce. Expõe window.klarimTrack e dispara
// page_view no load. Fire-and-forget: nunca quebra a página.
(function () {
  var SID_KEY = 'klarim_sid';
  var UTM_KEY = 'klarim_utm';

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

  function track(eventType, metadata, targetUrl) {
    try {
      var u = utm();
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
          metadata: metadata || {},
        }, u)),
      }).catch(function () {});
    } catch (e) { /* tracking nunca quebra a app */ }
  }

  window.klarimTrack = track;

  // page_view em cada página pública (o painel admin e o dashboard ficam de fora).
  var path = window.location.pathname;
  if (path.indexOf('/painel') !== 0 && path.indexOf('/dashboard') !== 0) {
    track('page_view');
    // profile_view em /site/{dominio} — mede tráfego dos perfis públicos (KL-51 f4).
    var m = path.match(/^\/site\/([^/]+)/);
    if (m) {
      var dom = decodeURIComponent(m[1]);
      track('profile_view', { domain: dom }, 'https://' + dom);
    }
  }
})();
