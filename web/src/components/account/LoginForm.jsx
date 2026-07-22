import { useState, useEffect } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';

export default function LoginForm({ redirect = '/dashboard', url = '', email: initialEmail = '' }) {
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [magic, setMagic] = useState({ state: 'idle', msg: '' }); // idle | sending | sent | not_found

  // KL-99: o /account/magic-access redireciona para /entrar?magic=expired quando o link vence.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (new URLSearchParams(window.location.search).get('magic') === 'expired') {
      setError('Seu link de acesso expirou. Solicite um novo abaixo.');
    }
  }, []);

  // KL-68: preserva url/email na navegação e reivindica o site ao entrar (claim).
  const navQ = new URLSearchParams();
  if (url) navQ.set('url', url);
  if (email) navQ.set('email', email);
  const qs = navQ.toString() ? `?${navQ}` : '';
  function nextUrl(data) {
    const c = data?.claim;
    if (c?.blocked_domain) return '/dashboard?blocked=1';
    if (c?.site_added && c?.domain) {
      return `/dashboard?${c.is_owner ? 'claimed' : 'added'}=${encodeURIComponent(c.domain)}`;
    }
    return redirect;
  }

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    const { ok, data, error: err } = await apiPost('/account/login', { email, password, url: url || undefined });
    setBusy(false);
    if (ok) { window.location.href = nextUrl(data); return; }
    setError(err || 'E-mail ou senha incorretos.');
  }

  // KL-99 — magic link: conta sem senha (ou quem esqueceu) recebe um link de acesso (TTL 1h).
  async function sendMagic() {
    setError('');
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)) {
      setMagic({ state: 'idle', msg: '' });
      setError('Digite seu e-mail acima para receber o link de acesso.');
      return;
    }
    setMagic({ state: 'sending', msg: '' });
    const { ok, status, data } = await apiPost('/account/magic-link', { email });
    if (ok && data.status === 'sent') {
      setMagic({ state: 'sent', msg: `Enviamos um link de acesso para ${email}. Verifique seu e-mail (vale 1 hora).` });
      return;
    }
    if (ok && data.status === 'not_found') {
      setMagic({ state: 'not_found', msg: '' });
      return;
    }
    if (status === 429) { setMagic({ state: 'idle', msg: '' }); setError('Muitos pedidos. Aguarde alguns minutos.'); return; }
    setMagic({ state: 'idle', msg: '' });
    setError('Não foi possível enviar o link. Tente novamente.');
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">Entrar no Klarim</h1>
      {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}

      {magic.state === 'sent' ? (
        <p className="mt-4 rounded-xl border border-brand-500/30 bg-brand-500/5 px-4 py-3 text-sm text-slate-200">
          📩 {magic.msg}
        </p>
      ) : (
        <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
          <div>
            <label htmlFor="email" className={label}>E-mail</label>
            <input id="email" type="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} autoComplete="email"
              placeholder="voce@empresa.com.br" className={field} />
          </div>
          <div>
            <label htmlFor="password" className={label}>Senha</label>
            <input id="password" type="password" required value={password}
              onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" className={field} />
          </div>
          <button type="submit" disabled={busy} className={btn}>{busy ? 'Entrando…' : 'Entrar →'}</button>
        </form>
      )}

      {magic.state !== 'sent' && (
        <div className="mt-4 border-t border-slate-800 pt-4">
          {magic.state === 'not_found' ? (
            <p className="text-sm text-slate-300">
              E-mail não encontrado.{' '}
              <a href={`/cadastrar${qs}`} className="text-brand-400 hover:text-brand-300">Cadastrar →</a>
            </p>
          ) : (
            <p className="text-sm text-slate-400">
              Não tem senha?{' '}
              <button type="button" onClick={sendMagic} disabled={magic.state === 'sending'}
                className="text-brand-400 hover:text-brand-300 disabled:opacity-60">
                {magic.state === 'sending' ? 'Enviando…' : 'Enviar link de acesso'}
              </button>
            </p>
          )}
        </div>
      )}

      <div className="mt-6 flex flex-col gap-1 text-sm">
        <a href={`/recuperar-senha${qs}`} className="text-slate-400 hover:text-white">Esqueci minha senha</a>
        <p className="text-slate-400">Não tem conta? <a href={`/cadastrar${qs}`} className="text-brand-400 hover:text-brand-300">Cadastrar →</a></p>
      </div>
    </div>
  );
}
