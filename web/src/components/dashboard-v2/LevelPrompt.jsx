// KL-99 — prompts de nível de conta no dashboard. Nível 1 (sem senha) → 2 (com senha) via
// SetPasswordModal; nível 2 → 3 (dono verificado por controle de domínio) via VerifyDomainModal.
//
// O hook `useLevelGate` intercepta ações sensíveis: se a conta não tem o nível exigido, abre o
// modal certo e, ao concluir, executa a AÇÃO ORIGINAL automaticamente (o usuário não clica de novo).
// O backend é a autoridade (403 {error:insufficient_level, required_level}); isto é só a UX.
import { useState, useCallback } from 'react';
import { apiPost } from '../../lib/api.js';
import Modal from './Modal.jsx';
import { card, brandBtn } from './shared.js';

const input =
  'h-12 w-full rounded-xl border border-slate-700 bg-slate-800/80 px-4 text-base text-white ' +
  'placeholder:text-slate-500 outline-none transition-colors focus:border-brand-500 focus:ring-2 focus:ring-brand-500/30';

// --- hook de gate ---------------------------------------------------------------------------- #
export function useLevelGate(user) {
  const [level, setLevel] = useState(() => Number(user?.account_level) || 2);
  const [prompt, setPrompt] = useState(null); // { requiredLevel, targetId, domain, action }

  // ensureLevel(n, {targetId, domain}, action): roda `action` se já tem nível n; senão abre o modal.
  const ensureLevel = useCallback((requiredLevel, opts, action) => {
    if (level >= requiredLevel) { action?.(); return; }
    setPrompt({ requiredLevel, targetId: opts?.targetId, domain: opts?.domain, action });
  }, [level]);

  function reached(newLevel) {
    setLevel((l) => Math.max(l, newLevel));
    const act = prompt?.action;
    setPrompt(null);
    act?.();  // executa a ação original AUTOMATICAMENTE (card 3a/3b)
  }

  const levelModal = !prompt ? null : (prompt.requiredLevel >= 3
    ? <VerifyDomainModal targetId={prompt.targetId} domain={prompt.domain}
        onClose={() => setPrompt(null)} onVerified={() => reached(3)} />
    : <SetPasswordModal onClose={() => setPrompt(null)} onDone={() => reached(2)} />);

  return { ensureLevel, levelModal, level };
}

// --- modal de senha (nível 1 → 2) ------------------------------------------------------------ #
export function SetPasswordModal({ onClose, onDone }) {
  const [pw, setPw] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function submit(e) {
    e.preventDefault(); setErr('');
    if (pw.length < 8) return setErr('A senha precisa ter ao menos 8 caracteres.');
    if (pw !== confirm) return setErr('As senhas não conferem.');
    setBusy(true);
    const { ok, data, error } = await apiPost('/account/set-password', { password: pw, confirm });
    setBusy(false);
    if (ok) { onDone(); return; }
    setErr(error || data?.detail || 'Não foi possível definir a senha.');
  }

  return (
    <Modal title="🔒 Defina uma senha para continuar" onClose={onClose}>
      <p className="text-sm text-slate-400">
        Para proteger sua conta, esta ação requer uma senha. É rápido.
      </p>
      <form onSubmit={submit} className="mt-4 flex flex-col gap-3">
        <input type="password" required minLength={8} value={pw} onChange={(e) => setPw(e.target.value)}
          autoComplete="new-password" placeholder="Nova senha (mín. 8)" className={input} />
        <input type="password" required value={confirm} onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password" placeholder="Confirmar senha" className={input} />
        {err && <p className="text-sm text-red-300">{err}</p>}
        <button type="submit" disabled={busy} className={`${brandBtn} w-full`}>
          {busy ? 'Salvando…' : 'Definir senha e continuar'}
        </button>
      </form>
    </Modal>
  );
}

// --- modal de verificação de domínio (nível 2 → 3) ------------------------------------------- #
const METHODS = [
  { key: 'meta_tag', label: 'Meta tag' },
  { key: 'html_file', label: 'Arquivo HTML' },
  { key: 'dns_txt', label: 'Registro DNS' },
];

export function VerifyDomainModal({ targetId, domain, onClose, onVerified }) {
  const [method, setMethod] = useState(null);
  const [instr, setInstr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');

  async function choose(m) {
    setMethod(m); setMsg(''); setInstr(null); setBusy(true);
    const { ok, data, error } = await apiPost(`/account/sites/${targetId}/verify/start`, { method: m });
    setBusy(false);
    if (ok) setInstr(data.instructions);
    else setMsg(error || data?.detail || 'Não foi possível iniciar a verificação.');
  }

  async function check() {
    setBusy(true); setMsg('');
    const { ok, data } = await apiPost(`/account/sites/${targetId}/verify/check`, {});
    setBusy(false);
    if (ok && data.status === 'verified') { onVerified(); return; }
    if (ok && data.status === 'not_found') {
      setMsg('Ainda não encontramos a verificação. Confirme que salvou a alteração e tente de novo (o DNS pode levar alguns minutos).');
    } else if (ok && data.status === 'no_pending') {
      setMsg('A verificação expirou. Escolha um método novamente.');
    } else {
      setMsg('Não foi possível verificar agora. Tente de novo em instantes.');
    }
  }

  return (
    <Modal title={`🔒 Verifique que você é o dono de ${domain || 'seu site'}`} wide onClose={onClose}>
      <p className="text-sm text-slate-400">
        Para editar o perfil público e exibir o selo, precisamos confirmar que você controla este domínio.
      </p>

      <div className="mt-4 flex flex-wrap gap-2">
        {METHODS.map((mth) => (
          <button key={mth.key} type="button" onClick={() => choose(mth.key)} disabled={busy}
            className={`inline-flex min-h-[44px] items-center rounded-xl border px-4 text-sm font-semibold transition-colors ${
              method === mth.key ? 'border-brand-500 bg-brand-500/10 text-brand-300'
                : 'border-slate-700 text-slate-200 hover:bg-slate-800'}`}>
            {mth.label}
          </button>
        ))}
      </div>

      {instr && (
        <div className="mt-4 space-y-2 rounded-xl border border-slate-800 bg-slate-950/60 p-4 text-sm">
          <p className="font-semibold text-white">{instr.title}</p>
          {instr.snippet && <CodeBlock text={instr.snippet} />}
          {instr.path && <p className="text-slate-300">Arquivo: <code className="text-brand-300">{instr.path}</code></p>}
          {instr.content && <CodeBlock text={instr.content} />}
          {instr.host && <p className="text-slate-300">Host: <code className="text-brand-300">{instr.host}</code> · Tipo: <code className="text-brand-300">{instr.record_type}</code></p>}
          {instr.value && <CodeBlock text={instr.value} />}
          <p className="text-xs text-slate-500">{instr.help}</p>
        </div>
      )}

      {msg && <p className="mt-3 text-sm text-amber-300">{msg}</p>}

      {instr && (
        <button type="button" onClick={check} disabled={busy} className={`${brandBtn} mt-4 w-full`}>
          {busy ? 'Verificando…' : 'Verificar agora'}
        </button>
      )}
    </Modal>
  );
}

function CodeBlock({ text }) {
  const [copied, setCopied] = useState(false);
  function copy() {
    navigator.clipboard?.writeText(text);
    setCopied(true); setTimeout(() => setCopied(false), 1500);
  }
  return (
    <div className="flex items-start gap-2">
      <pre className="flex-1 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-2 text-xs text-slate-300"><code>{text}</code></pre>
      <button type="button" onClick={copy} className="mt-0.5 shrink-0 rounded-lg border border-slate-700 px-2 py-1 text-xs text-slate-300 hover:bg-slate-800">
        {copied ? '✓' : 'copiar'}
      </button>
    </div>
  );
}

// --- indicador de nível (card 3c) ------------------------------------------------------------ #
export function LevelBadge({ level, onSetPassword, onVerify }) {
  if (level >= 3) {
    return (
      <div className={`${card} border-green-500/30 bg-green-500/5`}>
        <p className="flex items-center gap-2 text-sm font-semibold text-green-300">
          <span aria-hidden="true">✅</span> Dono verificado
        </p>
        <p className="mt-1 text-xs text-slate-400">Você comprovou o controle deste domínio. Selo e perfil público liberados.</p>
      </div>
    );
  }
  if (level === 2) {
    return (
      <div className={card}>
        <p className="text-sm font-semibold text-white">Conta verificada</p>
        <button type="button" onClick={onVerify}
          className="mt-1 inline-flex min-h-[44px] items-center text-sm text-brand-400 hover:text-brand-300">
          Verifique a propriedade para o selo de dono →
        </button>
      </div>
    );
  }
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <p className="text-sm font-semibold text-white">Conta básica</p>
      <button type="button" onClick={onSetPassword}
        className="mt-1 inline-flex min-h-[44px] items-center text-sm text-brand-400 hover:text-brand-300">
        Defina uma senha para desbloquear mais funcionalidades →
      </button>
    </div>
  );
}
