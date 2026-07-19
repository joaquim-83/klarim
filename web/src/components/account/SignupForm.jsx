import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';

// Cadastro (KL-82 Slice 2 — confiança progressiva). Signup SEM código: e-mail + senha →
// conta criada na hora (email_confirmed=false) + e-mail de boas-vindas com link p/ confirmar.
// O usuário já entra logado e cai no dashboard (com banner de "confirme seu e-mail"). O fluxo
// de código de 6 dígitos (KL-25/KL-44 F-03b) fica DORMENTE ao fim do arquivo (fallback).
export default function SignupForm({ email: initialEmail = '', url = '', redirect = '/dashboard', role = '', invite = '', plan = '' }) {
  const emailFromScan = !!initialEmail;
  const isTech = role === 'technician';   // KL-44 P3: perfil de profissional de TI
  const planName = plan === 'agency' ? 'Agency' : (plan === 'pro' ? 'Pro' : '');   // KL-44 P6
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // KL-68: preserva url/email na navegação para o login e monta o redirect pós-signup
  // (?claimed= quando virou dono; ?added= quando só monitorou).
  const navQ = new URLSearchParams();
  if (url) navQ.set('url', url);
  if (email) navQ.set('email', email);
  const loginHref = `/entrar${navQ.toString() ? `?${navQ}` : ''}`;
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
    if (password.length < 8) return setError('A senha precisa ter ao menos 8 caracteres.');
    if (password !== confirm) return setError('As senhas não coincidem.');
    setBusy(true);
    const { ok, status, data, error: err } = await apiPost('/account/signup', {
      email, password, url: url || undefined,
      role: role || undefined, invite: invite || undefined, plan: plan || undefined });
    setBusy(false);
    if (ok) { window.location.href = nextUrl(data); return; }
    if (status === 409) return setError('Já existe uma conta com este e-mail. Faça login.');
    if (status === 400 && /descart|permanente/i.test(err || '')) {
      return setError('Por favor, use um e-mail permanente para criar sua conta.');
    }
    if (status === 429) return setError('Limite de cadastros atingido. Tente novamente mais tarde.');
    setError(err || 'Não foi possível criar a conta.');
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">{isTech ? 'Crie seu perfil de profissional de TI' : (planName ? `Comece seu trial ${planName}` : 'Criar sua conta')}</h1>
      <p className="mt-2 text-sm text-slate-400">
        {isTech ? 'Gerencie os sites dos seus clientes em um só painel.'
          : planName ? `Teste o plano ${planName} por 30 dias, sem cartão. Depois sua conta continua no Gratuito automaticamente.`
          : (emailFromScan ? 'Seu e-mail já está verificado. Só falta uma senha.' : 'Monitore seu site gratuitamente.')}
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
        Já tem conta? <a href={loginHref} className="text-brand-400 hover:text-brand-300">Entrar →</a>
      </p>
    </div>
  );
}
