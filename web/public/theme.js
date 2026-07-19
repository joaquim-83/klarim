// KL-87 — toggle de tema (sol/lua). O anti-FOUC (inline no <head> do Base.astro) já definiu
// data-theme antes do paint; aqui só sincronizamos o ícone e tratamos o clique. Externo →
// coberto pela CSP `script-src 'self'`. Admin (/painel) força dark e NÃO carrega este script.
(function () {
  function syncIcon() {
    var t = document.documentElement.getAttribute('data-theme') || 'light';
    var moon = document.getElementById('theme-icon-light'); // mostra 🌙 quando LIGHT (clique → dark)
    var sun = document.getElementById('theme-icon-dark');    // mostra ☀️ quando DARK (clique → light)
    if (moon) moon.classList.toggle('hidden', t !== 'light');
    if (sun) sun.classList.toggle('hidden', t !== 'dark');
  }
  syncIcon();
  var btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', function () {
      var cur = document.documentElement.getAttribute('data-theme') || 'light';
      var next = cur === 'light' ? 'dark' : 'light';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('klarim-theme', next); } catch (e) { /* private mode */ }
      syncIcon();
    });
  }
})();
