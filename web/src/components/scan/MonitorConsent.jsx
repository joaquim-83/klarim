// KL-99 — consentimento de monitoramento ("Quer monitorar este site?"). SEM campo de e-mail.
// Dois modos:
//  · mode="alert"  → visitante da SESSÃO DO ALERTA (view-only, sem conta): clicar cria a conta SEM
//    senha + ativa o monitoramento + loga (`POST /account/monitor-from-alert`). É AQUI que a conta
//    nasce (o clique no link do alerta só deu a sessão de visualização).
//  · mode="account" → usuário JÁ logado (confirmed) adicionando um site (`POST /account/sites`).
import { useState, useEffect } from 'react';
import { monitorConsentCopy } from '../../lib/scanView.js';

const card = 'rounded-2xl border-2 border-brand-500 bg-brand-500/5 p-6 sm:p-7';
const btn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const btnGhost =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-slate-700 px-6 py-3.5 ' +
  'text-base font-semibold text-slate-200 transition-colors hover:bg-slate-800 active:scale-[0.98]';

export default function MonitorConsent({ domain = '', url = '', mode = 'account' }) {
  const copy = monitorConsentCopy(domain);
  const [state, setState] = useState('idle'); // idle | activating | active | already | exists | error
  const [msg, setMsg] = useState('');

  // KL-105 — logado (mode=account): se JÁ monitora este domínio, mostra o estado B ("você já
  // monitora") em vez de oferecer adicionar de novo. Auth opcional; falha → mantém o estado idle.
  useEffect(() => {
    if (mode !== 'account' || !domain) return;
    let alive = true;
    fetch(`/api/account/monitoring-status?domain=${encodeURIComponent(domain)}`, { credentials: 'include' })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (alive && d && d.logged_in && d.monitoring) setState('already'); })
      .catch(() => {});
    return () => { alive = false; };
  }, [mode, domain]);

  async function activate() {
    setState('activating'); setMsg('');
    window.klarimTrack?.('monitor_consent_clicked', { mode }, url);
    try {
      const path = mode === 'alert' ? '/api/account/monitor-from-alert' : '/api/account/sites';
      const body = mode === 'alert' ? {} : { url: domain ? `https://${domain}` : url };
      const res = await fetch(path, {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (mode === 'alert' && res.ok && data.status === 'existing_account') { setState('exists'); return; }
      if (res.ok) {
        window.klarimTrack?.('monitoring_activated', { domain, mode }, url);
        if (mode === 'alert') {
          // conta criada + logada no backend → vai para o dashboard.
          window.location.href = `/dashboard?monitoring=${encodeURIComponent(domain)}`;
          return;
        }
        setState('active'); return;
      }
      setState('error');
      setMsg(data.detail || 'Não foi possível ativar o monitoramento.');
    } catch {
      setState('error'); setMsg('Falha de conexão. Tente novamente.');
    }
  }

  if (state === 'active') {
    return (
      <div className={card}>
        <p className="text-2xl" aria-hidden="true">✅</p>
        <h3 className="mt-2 text-lg font-bold text-white">Monitoramento ativo</h3>
        <p className="mt-1 text-sm text-slate-300">Vamos avisar você se algo mudar em {domain || 'seu site'}.</p>
        <a href="/dashboard" className={`${btn} mt-4`}>Ir para o dashboard →</a>
      </div>
    );
  }

  // Estado B (KL-105) — logado e já monitorando este site.
  if (state === 'already') {
    return (
      <div className={card}>
        <p className="text-2xl" aria-hidden="true">✅</p>
        <h3 className="mt-2 text-lg font-bold text-white">Você já monitora este site.</h3>
        <p className="mt-1 text-sm text-slate-300">Acompanhe {domain || 'seu site'} no seu painel.</p>
        <a href="/dashboard" className={`${btn} mt-4`}>Painel de monitoramento →</a>
      </div>
    );
  }

  if (state === 'exists') {
    const loginHref = `/entrar?redirect=${encodeURIComponent('/dashboard')}`;
    return (
      <div className={card}>
        <h3 className="text-lg font-bold text-white">Você já tem conta.</h3>
        <p className="mt-1 text-sm text-slate-300">Entre para monitorar {domain || 'este site'}.</p>
        <a href={loginHref} className={`${btn} mt-4`}>Entrar →</a>
      </div>
    );
  }

  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">{copy.title}</h3>
      <ul className="mt-3 space-y-1.5 text-sm text-slate-300">
        {copy.benefits.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <span className="text-green-500" aria-hidden="true">✓</span>{b}
          </li>
        ))}
      </ul>
      {msg && <p className="mt-3 text-sm text-red-300">{msg}</p>}
      <button type="button" onClick={activate} disabled={state === 'activating'}
        className={`${state === 'error' ? btnGhost : btn} mt-4`}>
        {state === 'activating' ? 'Ativando…' : state === 'error' ? 'Tentar de novo' : copy.button}
      </button>
      <p className="mt-3 text-xs text-slate-500">{copy.note}</p>
    </div>
  );
}
