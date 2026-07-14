import { useState } from 'react';
import { apiPost, apiPut, apiDelete } from '../../lib/api.js';
import { card, field, label, errorBox, okBox, btn } from './ui.js';

// Gestão de conta (KL-57): dados pessoais, segurança (senha), plano e exclusão.
// Ilha React na página SSR /dashboard/conta (protegida pelo middleware).

function fmtDate(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleDateString('pt-BR', {
      day: '2-digit', month: '2-digit', year: 'numeric',
    });
  } catch {
    return '—';
  }
}

const ghostBtn =
  'inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700 px-5 py-2.5 ' +
  'text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-800 disabled:opacity-60';

export default function AccountSettings({ user = {} }) {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold text-white">Minha conta</h1>
      <PersonalData user={user} />
      <Security email={user.email} />
      <Plan user={user} />
      <DangerZone />
    </div>
  );
}

// --- Dados pessoais --------------------------------------------------------- #
function PersonalData({ user }) {
  const [name, setName] = useState(user.name || '');
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState('');
  const [err, setErr] = useState('');

  async function save(e) {
    e.preventDefault();
    setOk(''); setErr(''); setBusy(true);
    const { ok: good, error } = await apiPut('/account/me', { name });
    setBusy(false);
    if (good) setOk('Nome atualizado ✓');
    else setErr(error || 'Não foi possível salvar.');
  }

  return (
    <section className={card}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-brand-400/80">Dados pessoais</h2>
      <div className="mt-4">
        <span className={label}>E-mail</span>
        <p className="text-slate-300">{user.email} <span className="text-xs text-slate-500">(não editável)</span></p>
      </div>
      {ok && <p className={`${okBox} mt-4`}>{ok}</p>}
      {err && <p className={`${errorBox} mt-4`}>{err}</p>}
      <form onSubmit={save} className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className={label} htmlFor="acc-name">Nome</label>
          <input id="acc-name" value={name} onChange={(e) => setName(e.target.value)}
            placeholder="Seu nome" className={field} maxLength={120} />
        </div>
        <button type="submit" disabled={busy} className={`${btn} sm:w-auto`}>
          {busy ? 'Salvando…' : 'Salvar'}
        </button>
      </form>
    </section>
  );
}

// --- Segurança (alterar senha) ---------------------------------------------- #
function Security({ email }) {
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState('');
  const [nw, setNw] = useState('');
  const [conf, setConf] = useState('');
  const [busy, setBusy] = useState(false);
  const [ok, setOk] = useState('');
  const [err, setErr] = useState('');

  function reset() {
    setCur(''); setNw(''); setConf(''); setErr(''); setOpen(false);
  }

  async function submit(e) {
    e.preventDefault();
    setErr(''); setOk('');
    if (nw.length < 8) return setErr('A nova senha precisa ter ao menos 8 caracteres.');
    if (nw !== conf) return setErr('A confirmação não confere.');
    setBusy(true);
    const { ok: good, error } = await apiPost('/account/change-password', {
      current_password: cur, new_password: nw,
    });
    setBusy(false);
    if (good) {
      setOk('Senha alterada com sucesso ✓');
      setCur(''); setNw(''); setConf(''); setOpen(false);
      window.klarimTrack?.('password_changed', {});
    } else setErr(error || 'Não foi possível alterar a senha.');
  }

  return (
    <section className={card}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-brand-400/80">Segurança</h2>
      {ok && <p className={`${okBox} mt-4`}>{ok}</p>}
      {!open ? (
        <div className="mt-4 flex items-center justify-between">
          <p className="text-slate-300">Senha <span className="text-slate-500">••••••••</span></p>
          <button onClick={() => { setOpen(true); setOk(''); }} className="text-sm text-brand-400 hover:text-brand-300">
            Alterar senha →
          </button>
        </div>
      ) : (
        <form onSubmit={submit} className="mt-4 space-y-3">
          {err && <p className={errorBox}>{err}</p>}
          <div>
            <label className={label} htmlFor="pw-cur">Senha atual</label>
            <input id="pw-cur" type="password" required value={cur} autoComplete="current-password"
              onChange={(e) => setCur(e.target.value)} className={field} />
          </div>
          <div>
            <label className={label} htmlFor="pw-new">Nova senha</label>
            <input id="pw-new" type="password" required value={nw} autoComplete="new-password"
              onChange={(e) => setNw(e.target.value)} className={field} />
          </div>
          <div>
            <label className={label} htmlFor="pw-conf">Confirmar nova senha</label>
            <input id="pw-conf" type="password" required value={conf} autoComplete="new-password"
              onChange={(e) => setConf(e.target.value)} className={field} />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button type="submit" disabled={busy} className={`${btn} sm:w-auto`}>
              {busy ? 'Salvando…' : 'Salvar nova senha'}
            </button>
            <button type="button" onClick={reset} className={ghostBtn}>Cancelar</button>
          </div>
        </form>
      )}
    </section>
  );
}

// --- Plano ------------------------------------------------------------------ #
function Plan({ user }) {
  const planName = user.plan === 'free' || !user.plan ? 'Gratuito' : user.plan;
  const max = user.max_sites || 1;
  return (
    <section className={card}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-brand-400/80">Plano</h2>
      <p className="mt-4 text-slate-300">
        Plano atual: <span className="font-semibold text-white">{planName}</span>{' '}
        <span className="text-slate-500">({max} site{max > 1 ? 's' : ''})</span>
      </p>
      <p className="mt-1 text-sm text-slate-400">Conta criada em: {fmtDate(user.created_at)}</p>
      <p className="mt-4 rounded-xl border border-slate-800 bg-slate-950/50 px-4 py-3 text-sm text-slate-400">
        Upgrade para monitorar até 5 sites — em breve.
      </p>
    </section>
  );
}

// --- Zona de perigo (excluir conta) ----------------------------------------- #
function DangerZone() {
  const [open, setOpen] = useState(false);
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');

  async function del(e) {
    e.preventDefault();
    setErr(''); setBusy(true);
    const { ok, error } = await apiDelete('/account/me', { password: pw });
    if (ok) {
      window.klarimTrack?.('account_deleted', {});
      window.location.href = '/';
      return;
    }
    setBusy(false);
    setErr(error || 'Não foi possível excluir a conta.');
  }

  return (
    <section className={`${card} border-red-500/30`}>
      <h2 className="text-sm font-semibold uppercase tracking-wide text-red-400/90">Zona de perigo</h2>
      {!open ? (
        <div className="mt-4">
          <button onClick={() => setOpen(true)}
            className="inline-flex items-center justify-center rounded-xl border border-red-500/50 px-5 py-2.5 text-sm font-semibold text-red-300 transition-colors hover:bg-red-500/10">
            Excluir minha conta
          </button>
          <p className="mt-3 text-sm text-slate-400">
            Ao excluir, seus dados pessoais e histórico de consultas são removidos. Os
            sites monitorados são desvinculados. Esta ação é irreversível.
          </p>
        </div>
      ) : (
        <form onSubmit={del} className="mt-4 space-y-3">
          <p className="text-slate-200">⚠️ Tem certeza?</p>
          <p className="text-sm text-slate-400">
            Ao excluir, seus dados pessoais e histórico de consultas são removidos. Sites
            monitorados são desvinculados. Esta ação é irreversível.
          </p>
          {err && <p className={errorBox}>{err}</p>}
          <div>
            <label className={label} htmlFor="del-pw">Para confirmar, digite sua senha:</label>
            <input id="del-pw" type="password" required value={pw} autoComplete="current-password"
              onChange={(e) => setPw(e.target.value)} className={field} />
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <button type="submit" disabled={busy || !pw}
              className="inline-flex items-center justify-center rounded-xl bg-red-600 px-6 py-3.5 text-base font-semibold text-white transition-colors hover:bg-red-500 disabled:opacity-60">
              {busy ? 'Excluindo…' : 'Excluir definitivamente'}
            </button>
            <button type="button" onClick={() => { setOpen(false); setPw(''); setErr(''); }} className={ghostBtn}>
              Cancelar
            </button>
          </div>
        </form>
      )}
    </section>
  );
}
