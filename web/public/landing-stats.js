// KL-103 — social proof da landing: busca os 3 contadores ao vivo (GET /api/public/stats,
// cache 1h no backend) e atualiza a stats bar; se o fetch falhar, o fallback estático já no
// HTML (SSG) permanece — sem erro visível. Também rastreia o clique nos pills de setor
// (evento `sector_pill_click`, via window.klarimTrack do track.js). Asset EXTERNO (public/):
// passa na CSP `script-src 'self'` sem hash. Não bloqueia o render (roda async no fim do body).
(function () {
  var fmt;
  try { fmt = new Intl.NumberFormat('pt-BR'); } catch (e) { fmt = { format: function (n) { return String(n); } }; }

  // > 1000 → arredonda p/ a centena inferior com "+"; senão exato (ex.: setores). KL-103.
  function roundPlus(n) {
    if (n == null || isNaN(n)) return null;
    n = Number(n);
    if (n > 1000) return fmt.format(Math.floor(n / 100) * 100) + '+';
    return fmt.format(n);
  }

  function setStat(key, val) {
    var el = document.querySelector('[data-stat="' + key + '"]');
    if (el && val != null) el.textContent = val;
  }

  // 1) números ao vivo (fire-and-forget; o fallback do HTML cobre a falha)
  try {
    fetch('/api/public/stats', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r && r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        if (d.sites_analyzed) setStat('sites_analyzed', roundPlus(d.sites_analyzed));
        if (d.sectors) setStat('sectors', roundPlus(d.sectors));
        if (d.public_profiles) setStat('public_profiles', roundPlus(d.public_profiles));
      })
      .catch(function () {});
  } catch (e) { /* nunca quebra a landing */ }

  // 2) rastreio do clique nos pills (KL-57): sector_pill_click com o slug. Evento de AÇÃO →
  // dispara na hora com keepalive (sobrevive à navegação do <a>).
  try {
    var pills = document.querySelectorAll('[data-sector-pill]');
    for (var i = 0; i < pills.length; i++) {
      (function (a) {
        a.addEventListener('click', function () {
          try {
            if (window.klarimTrack) {
              window.klarimTrack('sector_pill_click', { sector: a.getAttribute('data-sector-pill') });
            }
          } catch (e) { /* tracking nunca quebra a navegação */ }
        });
      })(pills[i]);
    }
  } catch (e) { /* noop */ }
})();
