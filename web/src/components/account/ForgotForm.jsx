import { useState } from 'react';
import { apiPost } from '../../lib/api.js';
import { field, btn, card, label, errorBox, okBox } from './ui.js';

// Recuperação de senha em 2 passos na mesma tela: e-mail → código+nova senha.
export default function ForgotForm() {
  const [step, setStep] = useState('email'); // email | reset | done
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  async function requestCode(e) {
    e.preventDefault();
    setError(''); setBusy(true);
    const { ok, data } = await apiPost('/account/forgot', { email });
    setBusy(false);
    // Resposta é sempre genérica (anti-enumeração): avançamos para o passo do código.
    if (ok) { setNotice(data.message || 'Se houver uma conta, enviamos um código.'); setStep('reset'); }
    else setError('Não foi possível processar. Tente novamente.');
  }

  async function doReset(e) {
    e.preventDefault();
    setError('');
    if (password.length < 8) return setError('A senha precisa ter ao menos 8 caracteres.');
    if (password !== confirm) return setError('As senhas não coincidem.');
    setBusy(true);
    const { ok, error: err } = await apiPost('/account/reset', { email, code: code.trim(), new_password: password });
    setBusy(false);
    if (ok) setStep('done');
    else setError(err || 'Código inválido ou expirado.');
  }

  if (step === 'done') {
    return (
      <div className={card}>
        <h1 className="text-2xl font-bold text-white">Senha redefinida</h1>
        <p className="mt-3 text-slate-300">Sua senha foi atualizada com sucesso.</p>
        <a href="/entrar" className="mt-6 inline-block text-brand-400 hover:text-brand-300">Entrar →</a>
      </div>
    );
  }

  return (
    <div className={card}>
      <h1 className="text-2xl font-bold text-white">Recuperar senha</h1>
      {notice && <p className={`mt-4 ${okBox}`}>{notice}</p>}
      {error && <p className={`mt-4 ${errorBox}`}>{error}</p>}

      {step === 'email' ? (
        <form onSubmit={requestCode} className="mt-6 flex flex-col gap-4">
          <div>
            <label htmlFor="email" className={label}>E-mail da conta</label>
            <input id="email" type="email" required value={email}
              onChange={(e) => setEmail(e.target.value)} autoComplete="email"
              placeholder="voce@empresa.com.br" className={field} />
          </div>
          <button type="submit" disabled={busy} className={btn}>{busy ? 'Enviando…' : 'Enviar código →'}</button>
        </form>
      ) : (
        <form onSubmit={doReset} className="mt-6 flex flex-col gap-4">
          <div>
            <label htmlFor="code" className={label}>Código (6 dígitos)</label>
            <input id="code" inputMode="numeric" maxLength={6} required value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))}
              placeholder="000000" className={`${field} tracking-[0.4em]`} />
          </div>
          <div>
            <label htmlFor="password" className={label}>Nova senha</label>
            <input id="password" type="password" required minLength={8} value={password}
              onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
              placeholder="mínimo 8 caracteres" className={field} />
          </div>
          <div>
            <label htmlFor="confirm" className={label}>Confirmar nova senha</label>
            <input id="confirm" type="password" required value={confirm}
              onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" className={field} />
          </div>
          <button type="submit" disabled={busy} className={btn}>{busy ? 'Salvando…' : 'Redefinir senha'}</button>
          <button type="button" onClick={() => setStep('email')} className="text-sm text-slate-400 hover:text-white">← Trocar e-mail</button>
        </form>
      )}
    </div>
  );
}
