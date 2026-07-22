// KL-99 Fluxo D — CTA de cadastro SEM senha no resultado do scan (visitante orgânico, não logado).
// Só e-mail → POST /account/signup-inline → conta nível 1 + e-mail de confirmação (link POST-only).
// NÃO pede senha (a senha pode ser definida depois, no dashboard). Estados: idle → sending →
// (sent | exists | error). O e-mail que o usuário digitou é exibido no sucesso (input local).
import { useState } from 'react';
import { inlineSignupCopy } from '../../lib/scanView.js';

const card = 'rounded-2xl border border-brand-500/40 bg-brand-500/5 p-6 sm:p-7';
const btn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const input =
  'h-12 w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';

export default function InlineSignup({ domain = '', risksCount = 0, url = '' }) {
  const copy = inlineSignupCopy(risksCount);
  const [email, setEmail] = useState('');
  const [state, setState] = useState('idle'); // idle | sending | sent | exists | error
  const [msg, setMsg] = useState('');

  async function submit(e) {
    e.preventDefault();
    setState('sending'); setMsg('');
    window.klarimTrack?.('signup_inline_clicked', { via: 'organic' }, url);
    try {
      const res = await fetch('/api/account/signup-inline', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, domain }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'confirmation_sent') {
        window.klarimTrack?.('account_created', { via: 'inline' }, url);
        setState('sent'); return;
      }
      if (res.ok && data.status === 'already_exists') { setState('exists'); return; }
      if (res.status === 429) { setState('error'); setMsg('Tente novamente em alguns minutos.'); return; }
      if (res.status === 400) { setState('error'); setMsg('Use um e-mail permanente e válido.'); return; }
      setState('error'); setMsg(data.detail || 'Não foi possível cadastrar. Tente de novo.');
    } catch {
      setState('error'); setMsg('Falha de conexão. Tente novamente.');
    }
  }

  if (state === 'sent') {
    return (
      <div className={card}>
        <p className="text-2xl" aria-hidden="true">📩</p>
        <h3 className="mt-2 text-lg font-bold text-white">Enviamos um link para {email}.</h3>
        <p className="mt-1 text-sm text-slate-300">
          Clique no link do e-mail para ativar o monitoramento de {domain || 'seu site'}.
        </p>
        <p className="mt-3 text-xs text-slate-500">Não recebeu? Verifique o spam.</p>
      </div>
    );
  }

  if (state === 'exists') {
    const loginHref = `/entrar?redirect=${encodeURIComponent('/dashboard')}${email ? `&email=${encodeURIComponent(email)}` : ''}`;
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
      <h3 className="text-lg font-bold text-white">{copy.headline}</h3>
      <p className="mt-1 text-sm text-slate-300">{copy.subtitle}</p>
      <ul className="mt-3 space-y-1.5 text-sm text-slate-300">
        {copy.benefits.map((b) => (
          <li key={b} className="flex items-start gap-2">
            <span className="text-brand-400" aria-hidden="true">✓</span>{b}
          </li>
        ))}
      </ul>
      <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          autoComplete="email" placeholder="seu@email.com.br" className={input}
          disabled={state === 'sending'} />
        {msg && <p className="text-sm text-red-300">{msg}</p>}
        <button type="submit" disabled={state === 'sending'} className={btn}>
          {state === 'sending' ? 'Enviando…' : copy.button}
        </button>
      </form>
      <p className="mt-3 text-xs text-slate-500">{copy.note}</p>
    </div>
  );
}
