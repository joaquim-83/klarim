// KL-90 (regressão encontrada na investigação) — banner de "confirme seu e-mail" que o
// Dashboard de produção tinha e o v2 havia perdido. Só aparece p/ conta não confirmada.
import { useState } from 'react';
import { apiPost } from '../../lib/api.js';

export default function ConfirmEmailBanner({ user }) {
  const [state, setState] = useState('');
  if (!user || user.email_confirmed !== false) return null;

  async function resend() {
    setState('sending');
    const { ok, status } = await apiPost('/account/resend-confirmation', {});
    setState(ok ? 'sent' : status === 429 ? 'limit' : 'error');
  }

  return (
    <div className="rounded-xl border border-brand-500/30 bg-brand-500/10 px-4 py-3 text-sm">
      <p className="text-slate-200">
        📧 Confirme seu e-mail para desbloquear o relatório completo. Enviamos um link para <strong className="text-white">{user.email}</strong>.
      </p>
      {state === 'sent' ? <p className="mt-1 text-brand-300">Link reenviado ✓</p>
        : state === 'limit' ? <p className="mt-1 text-slate-400">Aguarde alguns minutos para reenviar.</p>
        : state === 'error' ? <p className="mt-1 text-red-300">Não foi possível reenviar agora.</p>
        : <button type="button" onClick={resend} disabled={state === 'sending'}
            className="mt-1 inline-flex min-h-[44px] items-center text-brand-400 hover:text-brand-300 disabled:opacity-60">
            {state === 'sending' ? 'Reenviando…' : 'Reenviar link de confirmação →'}
          </button>}
    </div>
  );
}
