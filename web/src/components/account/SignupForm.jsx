import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';

// Cadastro pós-scan (KL-51 f3). O e-mail já foi verificado no fluxo de scan (KL-25),
// então chega pré-preenchido (readonly) via query param; só falta a senha.
export default function SignupForm({ email: initialEmail = '', url = '', redirect = '/dashboard' }) {
  const emailFromScan = !!initialEmail;
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function submit(e) {
    e.preventDefault();
    setError('');
    if (password.length < 8) return setError('A senha precisa ter ao menos 8 caracteres.');
    if (password !== confirm) return setError('As senhas não coincidem.');
    setBusy(true);
    const { ok, status, data, error: err } = await apiPost('/account/signup', { email, password, url: url || undefined });
    setBusy(false);
    if (ok) { window.location.href = redirect; return; }
    if (status === 409) return setError('Já existe uma conta com este e-mail. Faça login.');
    setError(err || 'Não foi possível criar a conta.');
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">Criar sua conta</h1>
      <p className="mt-2 text-sm text-slate-400">
        {emailFromScan ? 'Seu e-mail já está verificado. Só falta uma senha.' : 'Monitore seu site gratuitamente.'}
      </p>
      {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
        <div>
          <label htmlFor="email" className={label}>E-mail</label>
          <input id="email" type="email" required value={email} readOnly={emailFromScan}
            onChange={(e) => setEmail(e.target.value)} autoComplete="email"
            className={`${field} ${emailFromScan ? 'opacity-70' : ''}`} />
        </div>
        <div>
          <label htmlFor="password" className={label}>Senha</label>
          <input id="password" type="password" required minLength={8} value={password}
            onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
            placeholder="mínimo 8 caracteres" className={field} />
        </div>
        <div>
          <label htmlFor="confirm" className={label}>Confirmar senha</label>
          <input id="confirm" type="password" required value={confirm}
            onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" className={field} />
        </div>
        <button type="submit" disabled={busy} className={btn}>{busy ? 'Criando…' : 'Criar conta →'}</button>
      </form>
      <p className="mt-6 text-sm text-slate-400">
        Já tem conta? <a href="/entrar" className="text-brand-400 hover:text-brand-300">Entrar →</a>
      </p>
    </div>
  );
}
