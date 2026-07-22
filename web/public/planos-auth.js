// KL-90 item 7 — página de Planos com experiência LOGADA. Quando há sessão, busca a
// assinatura e: destaca o plano atual ("Seu plano" + banner de status), e troca os CTAs
// para Fazer upgrade / Fazer downgrade / Plano atual. Deslogado → mantém os CTAs de cadastro.
// Script EXTERNO (script-src 'self' na CSP; sem hash inline).
(function () {
  var RANK = { free: 0, pro: 1, agency: 2 };
  var STATUS_PT = { trial: 'em trial', active: 'ativo', free: 'ativo', expired: 'expirado', cancelled: 'cancelado' };

  function run() {
    fetch('/api/account/subscription', { credentials: 'include' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (sub) {
        if (!sub || !sub.plan_id) return; // deslogado → mantém o padrão
        var cur = sub.plan_id;
        var curRank = RANK[cur] != null ? RANK[cur] : 0;

        // banner de status
        var banner = document.getElementById('plan-status');
        if (banner) {
          var nm = document.getElementById('plan-status-name');
          var ex = document.getElementById('plan-status-extra');
          if (nm) nm.textContent = sub.plan_name || cur;
          var extra = '· ' + (STATUS_PT[sub.status] || sub.status || '');
          if (sub.status === 'trial' && sub.trial_days_left != null) extra += ' · ' + sub.trial_days_left + ' dias restantes';
          if (ex) ex.textContent = extra;
          banner.classList.remove('hidden');
        }

        // cada card
        document.querySelectorAll('[data-plan-card]').forEach(function (cardEl) {
          var id = cardEl.getAttribute('data-plan-card');
          var cta = cardEl.querySelector('a[data-plan]');
          var isCurrent = id === cur;
          if (isCurrent) {
            var badge = cardEl.querySelector('[data-plan-badge]');
            if (badge) { badge.classList.remove('hidden'); badge.classList.add('inline-flex'); }
            cardEl.classList.add('ring-2', 'ring-green-500/50');
            var pop = cardEl.querySelector('[data-popular-badge]');
            if (pop) pop.classList.add('hidden');
          }
          if (!cta) return;
          if (isCurrent) {
            cta.textContent = 'Plano atual';
            cta.removeAttribute('href');
            cta.className = 'mt-6 rounded-xl px-5 py-3 text-center text-sm font-semibold border border-slate-800 text-slate-500 cursor-default';
          } else if (RANK[id] > curRank) {
            cta.textContent = 'Fazer upgrade →';
            cta.setAttribute('href', '/dashboard?upgrade=' + id);
          } else {
            cta.textContent = id === 'free' ? 'Fazer downgrade' : 'Mudar para ' + (cardEl.querySelector('h2') ? cardEl.querySelector('h2').textContent : id);
            cta.setAttribute('href', '/dashboard/conta');
          }
        });
      })
      .catch(function () { /* deslogado → mantém /cadastrar */ });
  }

  if (document.readyState !== 'loading') run();
  else document.addEventListener('DOMContentLoaded', run);
})();
