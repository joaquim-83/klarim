// KL-99 Fluxo D + KL-105 — CTA de cadastro SEM senha no resultado do scan (visitante orgânico, não
// logado). Só e-mail → POST /account/signup-inline → conta nível 1 + monitoramento ATIVADO NA HORA
// + login (sem confirmação prévia — lição KL-89). Estados: idle → sending → (active | exists | error).
// Conta já existente → dispara um magic link automaticamente (o usuário volta sem senha).
import { useState, useEffect } from 'react';
import { inlineSignupCopy, isValidEmail } from '../../lib/scanView.js';

const card = 'rounded-2xl border-2 border-brand-500 bg-brand-500/5 p-6 sm:p-7';
const btn =
  'inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl bg-brand-500 px-6 py-3.5 ' +
  'text-base font-semibold text-[var(--accent-text)] transition-colors hover:bg-brand-400 active:scale-[0.98] disabled:opacity-60';
const input =
  'h-12 w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';

export default function InlineSignup({ domain = '', risksCount = 0, url = '' }) {
  const copy = inlineSignupCopy(risksCount);
  const [email, setEmail] = useState('');
  const [state, setState] = useState('idle'); // idle | sending | active | exists | error
  const [msg, setMsg] = useState('');

  useEffect(() => {
    window.klarimTrack?.('inline_signup_shown', { risks: risksCount }, url);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function submit(e) {
    e.preventDefault();
    setState('sending'); setMsg('');
    window.klarimTrack?.('inline_signup_click', { via: 'organic' }, url);
    window.klarimTrack?.('signup_inline_clicked', { via: 'organic' }, url); // compat KL-82
    const clean = email.trim().toLowerCase();
    try {
      const res = await fetch('/api/account/signup-inline', {
        method: 'POST', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email: clean, domain }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.status === 'monitoring_active') {
        window.klarimTrack?.('inline_signup_success', { via: 'organic', domain }, url);
        window.klarimTrack?.('account_created', { via: 'inline' }, url); // compat KL-82
        setState('active'); return;
      }
      if (res.ok && data.status === 'already_exists') {
        window.klarimTrack?.('inline_signup_existing', { via: 'organic' }, url);
        // Conta já existe → envia um link de acesso (magic link) para o usuário voltar sem senha.
        fetch('/api/account/magic-link', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: clean }),
        }).catch(() => {});
        setState('exists'); return;
      }
      if (res.status === 429) { setState('error'); setMsg('Muitas tentativas. Aguarde um momento.'); return; }
      if (res.status === 400 || res.status === 422) { setState('error'); setMsg('Use um e-mail permanente e válido.'); return; }
      setState('error'); setMsg('Tente novamente em alguns segundos.');
    } catch {
      setState('error'); setMsg('Falha de conexão. Tente novamente.');
    }
  }

  if (state === 'active') {
    return (
      <div className={card}>
        <p className="text-2xl" aria-hidden="true">✅</p>
        <h3 className="mt-2 text-lg font-bold text-white">Monitoramento ativado!</h3>
        <p className="mt-1 text-sm text-slate-300">
          Você receberá alertas em {email} se algo mudar em {domain || 'seu site'}.
        </p>
        <a href="/dashboard" className={`${btn} mt-4`}>Ir para o dashboard →</a>
      </div>
    );
  }

  if (state === 'exists') {
    return (
      <div className={card}>
        <p className="text-2xl" aria-hidden="true">📩</p>
        <h3 className="mt-2 text-lg font-bold text-white">Conta já existe.</h3>
        <p className="mt-1 text-sm text-slate-300">
          Enviamos um link de acesso para {email}. Verifique seu e-mail (vale 1 hora).
        </p>
        <p className="mt-3 text-xs text-slate-500">Não recebeu? Verifique o spam.</p>
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
            <span className="text-green-500" aria-hidden="true">✓</span>{b}
          </li>
        ))}
      </ul>
      <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
          autoComplete="email" placeholder="seu@email.com" className={input}
          disabled={state === 'sending'} />
        {msg && <p className="text-sm text-red-300">{msg}</p>}
        <button type="submit" disabled={state === 'sending' || !isValidEmail(email)} className={btn}>
          {state === 'sending' ? 'Processando…' : copy.button}
        </button>
      </form>
      <p className="mt-3 text-xs text-slate-400">
        Ao clicar, você concorda com os{' '}
        <a href="/termos" className="underline hover:text-slate-200">Termos de Uso</a> e a{' '}
        <a href="/privacidade" className="underline hover:text-slate-200">Política de Privacidade</a>.
        Você pode cancelar a qualquer momento.
      </p>
      <p className="mt-2 text-center text-sm text-slate-400">{copy.note}</p>
    </div>
  );
}
