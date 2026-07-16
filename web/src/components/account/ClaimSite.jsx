import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '../../lib/api';
import OwnershipVerification from './OwnershipVerification';

// KL-68 — CTA de reivindicação, condicional ao estado do usuário (perfil público
// /site/{domain}). 4 estados: (1) deslogado → cadastrar; (2) logado sem monitorar →
// monitorar; (3) logado monitorando, sem dono → verificar; (4) logado dono verificado →
// painel. Domínio bloqueado (público/institucional) → mensagem educativa, sem claim.
const CARD = 'rounded-2xl border border-brand-500/30 bg-brand-500/5 p-6';

export default function ClaimSite({ url, domain, ownerVerified = false, claimable = true, blockMessage = '' }) {
  const [state, setState] = useState('loading'); // loading | logged_out | site
  const [site, setSite] = useState(null);         // {target_id, is_owner} se monitora
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);
  const [verifyOpen, setVerifyOpen] = useState(false);

  useEffect(() => {
    if (!claimable) return;
    (async () => {
      const me = await apiGet('/account/me');
      if (!me.ok) { setState('logged_out'); return; }
      const sites = await apiGet('/account/sites');
      const match = (sites.data?.sites || []).find(
        (s) => (s.domain || '').toLowerCase() === (domain || '').toLowerCase());
      setSite(match ? { target_id: match.target_id, is_owner: !!match.is_owner } : null);
      setState('site');
    })();
  }, [domain, claimable]);

  // Domínio público/institucional → não é monitorável (scan é livre; monitorar não).
  if (!claimable) {
    return (
      <div className="rounded-2xl border border-slate-800 bg-slate-900/60 p-6">
        <p className="text-sm text-slate-300">
          {blockMessage || 'Este é um domínio público. O Klarim monitora sites de empresas brasileiras.'}
        </p>
        <a href="/scan" className="mt-3 inline-flex text-sm font-semibold text-brand-400 hover:text-brand-300">
          Verificar meu site →
        </a>
      </div>
    );
  }

  if (state === 'loading') {
    return <div className={CARD}><p className="text-sm text-slate-400">Carregando…</p></div>;
  }

  // Estado 1 — deslogado.
  if (state === 'logged_out') {
    return (
      <div className={CARD}>
        <h2 className="text-lg font-bold text-white">É o seu site?</h2>
        <p className="mt-1 text-sm text-slate-300">
          Gerencie gratuitamente: monitore o score, receba alertas e melhore a segurança.
        </p>
        <a href={`/cadastrar?url=${encodeURIComponent(url)}`}
          className="mt-4 inline-flex rounded-xl bg-brand-500 px-6 py-3 text-sm font-semibold text-slate-950 hover:bg-brand-400">
          Reivindicar este site →
        </a>
      </div>
    );
  }

  // Estado 2 — logado, ainda não monitora.
  if (!site) {
    async function monitor() {
      setBusy(true); setMsg('');
      const { ok, data, error } = await apiPost('/account/sites', { url });
      setBusy(false);
      if (!ok) { setMsg(error || 'Não foi possível adicionar.'); return; }
      setSite({ target_id: data.target_id, is_owner: !!data.is_owner });
      setMsg(data.is_owner ? '✓ Propriedade verificada automaticamente.' : '');
    }
    return (
      <div className={CARD}>
        <h2 className="text-lg font-bold text-white">É o seu site?</h2>
        <p className="mt-1 text-sm text-slate-300">Adicione ao monitoramento gratuito e acompanhe o score.</p>
        <button disabled={busy} onClick={monitor}
          className="mt-4 inline-flex rounded-xl bg-brand-500 px-6 py-3 text-sm font-semibold text-slate-950 hover:bg-brand-400 disabled:opacity-50">
          Monitorar este site →
        </button>
        {msg && <p className="mt-2 text-sm text-slate-300">{msg}</p>}
      </div>
    );
  }

  // Estado 4 — logado, monitorando, dono verificado.
  if (site.is_owner) {
    return (
      <div className={CARD}>
        <p className="text-lg font-bold text-white">✓ Você é o dono verificado deste site.</p>
        <div className="mt-3 flex flex-wrap gap-3">
          <a href="/dashboard" className="rounded-xl bg-slate-800 px-5 py-2.5 text-sm font-semibold text-white hover:bg-slate-700">Acessar painel →</a>
          <a href="/dashboard/conta" className="rounded-xl border border-slate-700 px-5 py-2.5 text-sm text-slate-300 hover:bg-slate-800">Editar perfil →</a>
        </div>
      </div>
    );
  }

  // Estado 3 — logado, monitorando, NÃO é dono.
  return (
    <div className={CARD}>
      <p className="text-sm text-slate-300">Você monitora este site.</p>
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {!ownerVerified && !verifyOpen && (
          <button onClick={() => setVerifyOpen(true)}
            className="rounded-xl border border-brand-500/40 bg-brand-500/10 px-4 py-2 text-sm font-semibold text-brand-300 hover:bg-brand-500/20">
            Verificar propriedade →
          </button>
        )}
        <a href="/dashboard" className="rounded-xl bg-slate-800 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700">Acessar painel →</a>
      </div>
      {verifyOpen && (
        <div className="mt-4">
          <OwnershipVerification targetId={site.target_id} onVerified={() => setSite({ ...site, is_owner: true })} />
        </div>
      )}
      {ownerVerified && <p className="mt-2 text-sm text-slate-400">Este site já tem um dono verificado.</p>}
    </div>
  );
}
