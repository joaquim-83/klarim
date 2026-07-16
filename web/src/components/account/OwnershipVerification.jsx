import { useState } from 'react';
import { apiPost } from '../../lib/api';

// KL-68 — verificação de propriedade por código (Tier 2). Reutilizável no perfil público
// (ClaimSite) e no dashboard (SiteDetail). Recebe o target_id do site que o usuário
// monitora; o backend envia o código ao contact_email do site (nunca exposto — só o hint
// mascarado). `onVerified` avisa o pai para atualizar o estado.
export default function OwnershipVerification({ targetId, onVerified }) {
  const [phase, setPhase] = useState('idle'); // idle | code | done
  const [hint, setHint] = useState('');
  const [code, setCode] = useState('');
  const [msg, setMsg] = useState('');
  const [busy, setBusy] = useState(false);

  async function request() {
    setBusy(true); setMsg('');
    const { ok, data, error } = await apiPost('/account/ownership/request-verification', { target_id: targetId });
    setBusy(false);
    if (!ok) { setMsg(error || 'Não foi possível enviar o código.'); return; }
    setHint(data.email_hint || 'seu e-mail de contato');
    setPhase('code');
  }

  async function verify() {
    setBusy(true); setMsg('');
    const { ok, data, error } = await apiPost('/account/ownership/verify', { target_id: targetId, code: code.trim() });
    setBusy(false);
    if (ok && data.verified) { setPhase('done'); onVerified && onVerified(); return; }
    if (data && data.error === 'expired') { setMsg('Código expirado. Solicite um novo.'); return; }
    const rem = data && typeof data.attempts_remaining === 'number' ? data.attempts_remaining : null;
    setMsg(rem !== null ? `Código incorreto. ${rem} tentativa(s) restante(s).` : (error || 'Código incorreto.'));
  }

  if (phase === 'done') {
    return <p className="text-sm font-semibold text-green-400">✓ Propriedade verificada!</p>;
  }

  if (phase === 'code') {
    return (
      <div className="space-y-2">
        <p className="text-sm text-slate-300">
          Enviamos um código de 6 dígitos para <b className="text-white">{hint}</b>. Verifique a caixa de entrada.
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <input
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
            inputMode="numeric" placeholder="000000"
            className="w-32 rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-center font-mono tracking-[0.4em] text-white"
          />
          <button
            disabled={busy || code.length !== 6} onClick={verify}
            className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-brand-400 disabled:opacity-50">
            Verificar
          </button>
          <button onClick={request} disabled={busy} className="text-xs text-slate-400 hover:text-white">Reenviar código</button>
        </div>
        {msg && <p className="text-sm text-red-400">{msg}</p>}
      </div>
    );
  }

  return (
    <div>
      <button
        disabled={busy} onClick={request}
        className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-4 py-2 text-sm font-semibold text-brand-300 hover:bg-brand-500/20 disabled:opacity-50">
        Verificar propriedade →
      </button>
      {msg && <p className="mt-2 text-sm text-red-400">{msg}</p>}
    </div>
  );
}
