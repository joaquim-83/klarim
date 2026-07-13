import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';

export default function LoginForm({ redirect = '/dashboard' }) {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setError('');
    setBusy(true);
    const { ok, data, error: err } = await apiPost('/account/login', { email, password });
    setBusy(false);
    if (ok) { window.location.href = redirect; return; }
    setError(err || 'E-mail ou senha incorretos.');
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">Entrar no Klarim</h1>
      {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}
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
      <div className="mt-6 flex flex-col gap-1 text-sm">
        <a href="/recuperar-senha" className="text-slate-400 hover:text-white">Esqueci minha senha</a>
        <p className="text-slate-400">Não tem conta? <a href="/cadastrar" className="text-brand-400 hover:text-brand-300">Cadastrar →</a></p>
      </div>
    </div>
  );
}
