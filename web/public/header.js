// KL-90 UX (item 1) — estado logado do Header global: troca Entrar/Cadastrar por um
// menu de usuário (avatar + dropdown) e revela a busca persistente. Script EXTERNO
// (coberto por script-src 'self' na CSP → sem hash inline; mesmo padrão do theme.js).
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }
  ready(async function () {
    var out = document.querySelector('[data-auth="out"]');
    var inn = document.querySelector('[data-auth="in"]');
    var search = document.querySelector('[data-auth-search]');
    try {
      var res = await fetch('/api/account/me', { credentials: 'include' });
      if (res.ok) {
        var data = await res.json();
        var user = data.user || {};
        out && out.classList.add('hidden');
        if (inn) { inn.classList.remove('hidden'); inn.classList.add('flex'); }
        if (search) { search.classList.remove('hidden'); search.classList.add('sm:block'); }
        var nameEl = document.getElementById('user-name');
        var emailEl = document.getElementById('user-email');
        var initEl = document.getElementById('user-initial');
        if (nameEl) nameEl.textContent = user.name || 'Minha conta';
        if (emailEl) emailEl.textContent = user.email || '';
        if (initEl) initEl.textContent = ((user.name || user.email || '?').trim().charAt(0) || '?').toUpperCase();
      }
    } catch (e) { /* deslogado: mantém o padrão */ }

    // dropdown do usuário
    var btn = document.getElementById('user-menu-btn');
    var menu = document.getElementById('user-menu');
    if (btn && menu) {
      btn.addEventListener('click', function (e) {
        e.stopPropagation();
        menu.classList.toggle('hidden');
      });
      document.addEventListener('click', function (e) {
        if (!menu.contains(e.target) && e.target !== btn) menu.classList.add('hidden');
      });
    }

    // logout
    var lo = document.querySelector('[data-logout]');
    if (lo) lo.addEventListener('click', async function () {
      try { await fetch('/api/account/logout', { method: 'POST', credentials: 'include' }); } catch (e) {}
      window.location.href = '/';
    });
  });
})();
