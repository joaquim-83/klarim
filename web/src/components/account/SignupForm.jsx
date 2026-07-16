import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';

// Cadastro (KL-51 f3 + KL-44 F-03b). Se o e-mail já foi verificado no scan (KL-25),
// o backend cria a conta direto (chega pré-preenchido via query param). Se NÃO foi
// verificado (cadastro direto), o backend responde `verification_sent` e a UI pede o
// código de 6 dígitos enviado por e-mail (fecha o gap de cadastro com e-mail de terceiro).
export default function SignupForm({ email: initialEmail = '', url = '', redirect = '/dashboard', role = '', invite = '' }) {
  const emailFromScan = !!initialEmail;
  const isTech = role === 'technician';   // KL-44 P3: perfil de profissional de TI
  const [step, setStep] = useState('form');   // 'form' | 'code'
  const [email, setEmail] = useState(initialEmail);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [code, setCode] = useState('');
  const [maskedEmail, setMaskedEmail] = useState('');
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
      role: role || undefined, invite: invite || undefined });
    setBusy(false);
    if (ok && data?.status === 'verification_sent') {
      setMaskedEmail(data.email || email);
      setStep('code');
      return;
    }
    if (ok) { window.location.href = nextUrl(data); return; }
    if (status === 409) return setError('Já existe uma conta com este e-mail. Faça login.');
    setError(err || 'Não foi possível criar a conta.');
  }

  async function verify(e) {
    e.preventDefault();
    setError('');
    if (!/^\d{6}$/.test(code.trim())) return setError('Digite o código de 6 dígitos.');
    setBusy(true);
    const { ok, status, data, error: err } = await apiPost('/account/verify', { email, code: code.trim() });
    setBusy(false);
    if (ok) { window.location.href = nextUrl(data); return; }
    if (status === 409) { setError('Já existe uma conta com este e-mail. Faça login.'); return; }
    if (status === 400 || status === 429) return setError(err || 'Código inválido ou expirado.');
    setError(err || 'Não foi possível confirmar o código.');
  }

  async function resend() {
    setError('');
    setBusy(true);
    const { ok, error: err } = await apiPost('/account/signup', {
      email, password, url: url || undefined, role: role || undefined, invite: invite || undefined });
    setBusy(false);
    if (!ok) setError(err || 'Não foi possível reenviar o código.');
  }

  if (step === 'code') {
    return (
      <div className={card}>
        <h1 className="text-2xl font-bold text-white">Confirme seu e-mail</h1>
        <p className="mt-2 text-sm text-slate-400">
          Enviamos um código de 6 dígitos para <strong className="text-slate-200">{maskedEmail}</strong>. Digite-o abaixo para criar sua conta.
        </p>
        {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}
        <form onSubmit={verify} className="mt-6 flex flex-col gap-4">
          <div>
            <label htmlFor="code" className={label}>Código de verificação</label>
            <input id="code" inputMode="numeric" autoComplete="one-time-code" required
              value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="000000" className={`${field} tracking-[0.5em] text-center text-lg`} />
          </div>
          <button type="submit" disabled={busy} className={btn}>{busy ? 'Confirmando…' : 'Confirmar e criar conta →'}</button>
        </form>
        <p className="mt-6 text-sm text-slate-400">
          Não recebeu?{' '}
          <button type="button" onClick={resend} disabled={busy} className="text-brand-400 hover:text-brand-300 disabled:opacity-50">Reenviar código</button>
        </p>
      </div>
    );
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">{isTech ? 'Crie seu perfil de profissional de TI' : 'Criar sua conta'}</h1>
      <p className="mt-2 text-sm text-slate-400">
        {isTech ? 'Gerencie os sites dos seus clientes em um só painel.'
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
