// KL-99 Fluxo C — consentimento de monitoramento para visitante JÁ logado (ex.: chegou pelo link
// do alerta e a conta foi criada/logada automaticamente, ou é um usuário logado vendo um scan).
// SEM campo de e-mail — só o botão "Sim, monitorar" → POST /account/sites {url}. O monitoramento
// só começa com este consentimento explícito (nunca automático). Estados: idle → activating →
// (active | error).
import { useState } from 'react';
import { monitorConsentCopy } from '../../lib/scanView.js';

const card = 'rounded-2xl border border-brand-500/40 bg-brand-500/5 p-6 sm:p-7';
const btn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const btnGhost =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl border border-slate-700 px-6 py-3.5 ' +
  'text-base font-semibold text-slate-200 transition-colors hover:bg-slate-800 active:scale-[0.98]';

export default function MonitorConsent({ domain = '', url = '' }) {
  const copy = monitorConsentCopy(domain);
  const [state, setState] = useState('idle'); // idle | activating | active | error
  const [msg, setMsg] = useState('');

  async function activate() {
    setState('activating'); setMsg('');
    window.klarimTrack?.('monitor_consent_clicked', {}, url);
    try {
      const res = await fetch('/api/account/sites', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: domain ? `https://${domain}` : url }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        window.klarimTrack?.('monitoring_activated', { domain }, url);
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
        <p className="mt-1 text-sm text-slate-300">
          Vamos avisar você se algo mudar em {domain || 'seu site'}.
        </p>
        <a href="/dashboard" className={`${btn} mt-4`}>Ir para o dashboard →</a>
      </div>
    );
  }

  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">{copy.title}</h3>
      <ul className="mt-3 space-y-1.5 text-sm text-slate-300">
        {copy.benefits.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <span className="text-brand-400" aria-hidden="true">✓</span>{b}
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
