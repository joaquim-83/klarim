import { useEffect, useState, useCallback } from 'react';
import { apiGet, apiPost } from '../../lib/api.js';
import { card } from './ui.js';

// KL-44 P6 — seção de plano no dashboard: trial (countdown + upgrade), pago (downgrade),
// free (upgrade). Checkout PIX transparente (QR + copia-e-cola) com polling de status.
const PLAN_LABEL = { free: 'Gratuito', pro: 'Pro', agency: 'Agency' };
const PLAN_PRICE = { pro: 'R$ 19/mês', agency: 'R$ 49/mês' };
const RANK = { free: 0, pro: 1, agency: 2 };
// KL-44 P6: o que cada plano inclui (para o modal de comparação antes do PIX).
const PLAN_FEATURES = {
  free: ['1 site monitorado', 'Boletim mensal', 'Sem vigílias'],
  pro: ['5 sites monitorados', 'Boletim semanal', 'Vigílias core + uptime (30 min)', 'Selo Klarim', 'Vincular técnico'],
  agency: ['15 sites monitorados', 'Boletim diário', 'Todas as vigílias (uptime 5 min, mudanças, phishing)', 'Selo Klarim', 'Vincular técnico'],
};

function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString('pt-BR'); } catch { return '—'; }
}
function fmtAmount(cents) { return `R$ ${(cents / 100).toFixed(2).replace('.', ',')}`; }

function UpgradeModal({ plan, currentPlan = 'free', onClose, onActive }) {
  const [charge, setCharge] = useState(null);   // {charge_id, br_code, br_code_base64}
  const [err, setErr] = useState('');
  // KL-44 P6: começa em 'compare' (mostra o que o plano inclui) → só gera o QR ao confirmar.
  const [status, setStatus] = useState('compare'); // compare | creating | pending | paid | error
  const [copied, setCopied] = useState(false);

  // Gera a cobrança PIX só APÓS o usuário confirmar (não na montagem).
  async function confirmUpgrade() {
    setStatus('creating');
    const { ok, data, error } = await apiPost('/account/upgrade', { plan });
    if (!ok) { setErr(error || 'Não foi possível iniciar o pagamento.'); setStatus('error'); return; }
    setCharge(data); setStatus('pending');
  }

  // Polling do status enquanto pendente.
  useEffect(() => {
    if (status !== 'pending' || !charge?.charge_id) return;
    const id = setInterval(async () => {
      const { ok, data } = await apiGet(`/account/upgrade/status?charge_id=${encodeURIComponent(charge.charge_id)}`);
      if (ok && data.paid) { clearInterval(id); setStatus('paid'); onActive && onActive(); }
    }, 5000);
    return () => clearInterval(id);
  }, [status, charge, onActive]);

  const qr = charge?.br_code_base64
    ? (charge.br_code_base64.startsWith('data:') ? charge.br_code_base64 : `data:image/png;base64,${charge.br_code_base64}`)
    : null;

  function copyCode() {
    if (charge?.br_code) { navigator.clipboard?.writeText(charge.br_code); setCopied(true); setTimeout(() => setCopied(false), 2000); }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 px-4" onClick={onClose}>
      <div className={`${card} w-full max-w-md`} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between">
          <h3 className="text-lg font-bold text-white">Upgrade para {PLAN_LABEL[plan]} · {PLAN_PRICE[plan]}</h3>
          <button onClick={onClose} className="text-slate-500 hover:text-white">✕</button>
        </div>
        {status === 'compare' && (
          <div className="mt-4 space-y-4">
            <div>
              <p className="text-xs font-semibold uppercase text-slate-500">Seu plano atual: {PLAN_LABEL[currentPlan]}</p>
              <ul className="mt-1 space-y-1 text-sm text-slate-400">
                {(PLAN_FEATURES[currentPlan] || []).map((f) => <li key={f}>• {f}</li>)}
              </ul>
            </div>
            <div className="rounded-lg border border-brand-500/30 bg-brand-500/5 p-3">
              <p className="text-xs font-semibold uppercase text-brand-300">Upgrade para {PLAN_LABEL[plan]} — {PLAN_PRICE[plan]}</p>
              <ul className="mt-1 space-y-1 text-sm text-slate-200">
                {(PLAN_FEATURES[plan] || []).map((f) => <li key={f} className="flex gap-2"><span className="text-brand-400">✓</span>{f}</li>)}
              </ul>
            </div>
            <div className="flex gap-2">
              <button onClick={confirmUpgrade} className="flex-1 rounded-xl bg-brand-500 px-4 py-3 text-sm font-semibold text-slate-950 hover:bg-brand-400">
                Confirmar upgrade → {PLAN_PRICE[plan]}
              </button>
              <button onClick={onClose} className="rounded-xl border border-slate-700 px-4 py-3 text-sm text-slate-300 hover:bg-slate-800">Cancelar</button>
            </div>
          </div>
        )}
        {status === 'creating' && <p className="mt-4 text-sm text-slate-400">Gerando cobrança PIX…</p>}
        {status === 'error' && <p className="mt-4 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{err}</p>}
        {status === 'pending' && charge && (
          <div className="mt-4">
            <p className="text-sm text-slate-300">Pague com PIX para ativar. A confirmação é automática.</p>
            {qr && <img src={qr} alt="QR Code PIX" className="mx-auto mt-4 h-52 w-52 rounded-lg bg-white p-2" />}
            {charge.br_code && (
              <div className="mt-4">
                <p className="text-xs text-slate-500">PIX copia-e-cola:</p>
                <div className="mt-1 flex items-center gap-2">
                  <code className="flex-1 truncate rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300">{charge.br_code}</code>
                  <button onClick={copyCode} className="shrink-0 rounded-lg border border-slate-700 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-800">{copied ? 'Copiado ✓' : 'Copiar'}</button>
                </div>
              </div>
            )}
            <p className="mt-4 flex items-center gap-2 text-sm text-slate-400"><span className="h-2 w-2 animate-pulse rounded-full bg-yellow-400" />Aguardando pagamento…</p>
          </div>
        )}
        {status === 'paid' && (
          <div className="mt-4 rounded-lg border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-300">
            ✅ Pagamento confirmado! Plano {PLAN_LABEL[plan]} ativo.
            <button onClick={onClose} className="mt-3 block rounded-lg bg-brand-500 px-4 py-2 font-semibold text-slate-950 hover:bg-brand-400">Fechar</button>
          </div>
        )}
      </div>
    </div>
  );
}

export default function PlanSection({ initialUpgrade = '', showUpgradedToast = false }) {
  const [sub, setSub] = useState(null);
  const [payments, setPayments] = useState(null);
  const [modalPlan, setModalPlan] = useState(initialUpgrade && ['pro', 'agency'].includes(initialUpgrade) ? initialUpgrade : '');
  const [toast, setToast] = useState(showUpgradedToast ? '⏳ Pagamento em processamento… aguarde alguns segundos.' : '');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const { ok, data } = await apiGet('/account/subscription');
    if (ok) setSub(data);
    const p = await apiGet('/account/payments');
    if (p.ok) setPayments(p.data.payments || []);
  }, []);
  useEffect(() => { load(); }, [load]);

  // ?upgraded=1 → o redirect do PIX pode chegar antes do webhook: faz polling da assinatura.
  useEffect(() => {
    if (!showUpgradedToast) return;
    let n = 0;
    const id = setInterval(async () => {
      n += 1;
      const { ok, data } = await apiGet('/account/subscription');
      if (ok && data.status === 'active') {
        clearInterval(id); setSub(data);
        setToast(`✅ Upgrade para ${PLAN_LABEL[data.plan_id] || data.plan_id} confirmado!`);
      }
      if (n >= 24) clearInterval(id); // ~2 min
    }, 5000);
    return () => clearInterval(id);
  }, [showUpgradedToast]);

  async function downgrade(plan) {
    if (!confirm(`Mudar para o plano ${PLAN_LABEL[plan]}? Você perderá o monitoramento avançado do plano atual (os sites e o histórico são mantidos).`)) return;
    setBusy(true);
    const { ok, error } = await apiPost('/account/downgrade', { plan });
    setBusy(false);
    if (ok) { setToast(`Plano alterado para ${PLAN_LABEL[plan]}.`); load(); }
    else setToast(error || 'Não foi possível alterar o plano.');
  }

  if (!sub) return <div className={card}><p className="text-sm text-slate-400">Carregando plano…</p></div>;

  const plan = sub.plan_id || 'free';
  const status = sub.status;
  const isTrial = status === 'trial';

  return (
    <div className={card}>
      {toast && <div className="mb-4 rounded-lg border border-brand-500/40 bg-brand-500/10 px-4 py-2.5 text-sm text-brand-200">{toast}</div>}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-sm text-slate-400">Plano</p>
          <p className="mt-1 text-lg font-semibold text-white">
            {PLAN_LABEL[plan] || plan}
            {isTrial && <span className="ml-2 rounded bg-yellow-500/15 px-1.5 py-0.5 text-xs text-yellow-300">trial</span>}
            {status === 'active' && <span className="ml-2 text-green-400">✅</span>}
          </p>
          {isTrial && sub.trial_days_left != null && (
            <p className="mt-1 text-sm text-slate-400">Expira em {sub.trial_days_left} dia(s) · {fmtDate(sub.trial_ends_at)}</p>
          )}
          {status === 'active' && sub.started_at && <p className="mt-1 text-sm text-slate-500">Desde {fmtDate(sub.started_at)}</p>}
          {status === 'free' && <p className="mt-1 text-sm text-slate-500">Acesso limitado (1 site, boletim mensal).</p>}
        </div>
        <div className="flex flex-wrap gap-2">
          {RANK[plan] < RANK.pro && (
            <button onClick={() => setModalPlan('pro')} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-brand-400">Upgrade Pro → R$19/mês</button>
          )}
          {RANK[plan] < RANK.agency && (
            <button onClick={() => setModalPlan('agency')} className="rounded-lg border border-slate-700 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-slate-800">Upgrade Agency → R$49/mês</button>
          )}
          {plan === 'agency' && <button disabled={busy} onClick={() => downgrade('pro')} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-400 hover:bg-slate-800">Mudar p/ Pro</button>}
          {RANK[plan] > RANK.free && <button disabled={busy} onClick={() => downgrade('free')} className="rounded-lg border border-slate-700 px-4 py-2 text-sm text-slate-400 hover:bg-slate-800">Mudar p/ Gratuito</button>}
        </div>
      </div>

      {payments && payments.length > 0 && (
        <div className="mt-5 border-t border-slate-800 pt-4">
          <p className="text-xs font-semibold uppercase text-slate-500">Pagamentos</p>
          <ul className="mt-2 space-y-1 text-sm">
            {payments.map((p, i) => (
              <li key={i} className="flex flex-wrap items-center gap-x-4 text-slate-400">
                <span>{fmtDate(p.paid_at || p.created_at)}</span>
                <span className="text-slate-300">{PLAN_LABEL[p.plan] || p.plan} {fmtAmount(p.amount)}</span>
                <span className={p.status === 'paid' ? 'text-green-400' : p.status === 'expired' ? 'text-red-400' : 'text-yellow-400'}>
                  {p.status === 'paid' ? '✅ Pago' : p.status === 'expired' ? 'Expirado' : 'Pendente'}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {modalPlan && <UpgradeModal plan={modalPlan} currentPlan={plan} onClose={() => { setModalPlan(''); load(); }} onActive={() => { load(); }} />}
    </div>
  );
}
