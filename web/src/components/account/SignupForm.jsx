import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox } from './ui.js';
import { signupBody } from '../../lib/gate/ux.js';

// Cadastro SEM senha (KL-99 — conta sem fricção). Um único campo (e-mail) → POST /account/signup
// sem senha → conta nível 1 + e-mail de confirmação com link. O usuário já entra logado e cai no
// dashboard (com banner de "confirme seu e-mail"); a senha pode ser definida depois, quando uma
// ação sensível exigir (prompt de nível 1 → 2). Preserva url/role/invite/plan (KL-68/KL-44).
// KL-153 P2: `source='security-gate'` → conta developer + API key (redirect p/ /dashboard/gate).
export default function SignupForm({ email: initialEmail = '', url = '', redirect = '/dashboard', role = '', invite = '', plan = '', source = '' }) {
  const emailFromScan = !!initialEmail;
  const isDev = source === 'security-gate';   // KL-153 P2
  const isTech = role === 'technician';   // KL-44 P3: perfil de profissional de TI
  const planName = plan === 'agency' ? 'Agency' : (plan === 'pro' ? 'Pro' : '');   // KL-44 P6
  const [email, setEmail] = useState(initialEmail);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  // KL-68: preserva url/email na navegação para o login e monta o redirect pós-signup
  // (?claimed= quando virou dono; ?added= quando só monitorou).
  const navQ = new URLSearchParams();
  if (url) navQ.set('url', url);
  if (email) navQ.set('email', email);
  // KL-157 (Fix 4): fluxo dev → o "Faça login" leva de volta ao portal do Gate após entrar.
  // KL-150 (Fix 2): usa o `redirect` recebido (inclui ?upgrade= quando veio de "Assinar Pro/Team").
  if (isDev) navQ.set('redirect', redirect || '/dashboard/gate');
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
    setBusy(true);
    // KL-99: sem `password` → conta nível 1 (sem senha). O backend envia o link de confirmação.
    // KL-153: `source='security-gate'` cria conta developer + API key (via signupBody).
    const { ok, status, data, error: err } = await apiPost('/account/signup',
      signupBody({ email, url: url || undefined, role: role || undefined,
        invite: invite || undefined, plan: plan || undefined, source: source || undefined }));
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
      <h1 className="text-2xl font-bold text-white">{isDev ? 'Comece com o Security Gate' : isTech ? 'Crie seu perfil de profissional de TI' : (planName ? `Comece seu trial ${planName}` : 'Criar sua conta')}</h1>
      <p className="mt-2 text-sm text-slate-400">
        {isDev ? 'Conta de desenvolvedor + API key na hora. Escaneie seu deploy e integre no CI/CD.'
          : isTech ? 'Gerencie os sites dos seus clientes em um só painel.'
          : planName ? `Teste o plano ${planName} por 30 dias, sem cartão. Depois sua conta continua no Gratuito automaticamente.`
          : 'Só o seu e-mail. Sem senha para começar — você define depois, se quiser.'}
      </p>
      {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}
      <form onSubmit={submit} className="mt-6 flex flex-col gap-4">
        <div>
          <label htmlFor="email" className={label}>E-mail</label>
          <input id="email" type="email" required value={email} readOnly={emailFromScan}
            onChange={(e) => setEmail(e.target.value)} autoComplete="email"
            placeholder="voce@empresa.com.br"
            className={`${field} ${emailFromScan ? 'opacity-70' : ''}`} />
        </div>
        <button type="submit" disabled={busy} className={btn}>{busy ? 'Criando…' : 'Criar conta →'}</button>
      </form>
      <p className="mt-4 text-xs text-slate-500">
        Enviamos um link de confirmação para o seu e-mail. Sem cartão. Cancele quando quiser.
      </p>
      <p className="mt-6 text-sm text-slate-400">
        Já tem conta? <a href={loginHref} className="text-brand-400 hover:text-brand-300">Entrar →</a>
      </p>
    </div>
  );
}
