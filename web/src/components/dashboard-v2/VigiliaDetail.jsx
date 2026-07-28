// KL-123 — card de vigília EXPANSÍVEL. Fechado, mostra rótulo + status (+ badge de
// pendências). Ao clicar, faz lazy-load de `GET /account/sites/{id}/vigilias/{tipo}/details`
// (uma vez; cacheia no state), mostra os dados contextuais em linguagem acessível ao dono,
// orientação prática e ações (verificar domínio, denunciar, "não é ameaça", resolver).
import { useState } from 'react';
import { apiGet, apiPost } from '../../lib/api.js';
import { fmtDate } from './shared.js';
import {
  statusMeta, showBadge, vigiliaLabel, emailStateLabel, applyDismiss, pendingAlerts,
} from '../../lib/vigiliaDetail.js';

const track = (type, meta) => { try { window.klarimTrack?.(type, meta); } catch { /* nunca quebra */ } };

export default function VigiliaDetail({ targetId, domain, tipo, lastStatus }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [details, setDetails] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const st = statusMeta(details?.status || lastStatus || 'ok');
  const pending = details?.pending_count;
  const label = vigiliaLabel(tipo);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next) return;
    track('vigilia_expand', { tipo, domain });
    if (details || loading) return;            // lazy load: só na 1ª expansão
    setLoading(true); setError('');
    const { ok, data } = await apiGet(`/account/sites/${targetId}/vigilias/${tipo}/details`);
    if (ok) setDetails(data); else setError('Não foi possível carregar os detalhes agora.');
    setLoading(false);
  }

  async function dismiss(alertId) {
    if (busy) return;
    setBusy(true);
    track('vigilia_dismiss', { tipo, domain });
    const { ok } = await apiPost(`/account/sites/${targetId}/vigilias/phishing/dismiss/${alertId}`);
    if (ok) setDetails((d) => applyDismiss(d, alertId));   // otimista, sem recarregar
    setBusy(false);
  }

  async function acknowledge() {
    if (busy) return;
    setBusy(true);
    track('vigilia_action_click', { tipo, domain, action: 'acknowledge' });
    const { ok } = await apiPost(`/account/sites/${targetId}/vigilias/${tipo}/acknowledge`);
    if (ok) setDetails((d) => (d ? { ...d, pending_count: 0 } : d));
    setBusy(false);
  }

  function runAction(a) {
    if (a.type === 'link') { track('vigilia_action_click', { tipo, domain, label: a.label }); return; }
    if (a.endpoint === 'acknowledge') acknowledge();
  }

  return (
    <div className="overflow-hidden rounded-lg border border-slate-800 bg-slate-950/40">
      <button type="button" onClick={toggle} aria-expanded={open}
        className="flex min-h-[44px] w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-slate-800/40 active:scale-[0.99]">
        <span aria-hidden="true">{st.icon}</span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 text-sm font-medium text-slate-100">
            <span className="truncate">{label}</span>
            {showBadge(pending) && (
              <span className="rounded-full bg-red-500/20 px-2 py-0.5 text-xs font-bold text-red-400">{pending}</span>
            )}
          </span>
          {details?.summary && <span className="block truncate text-xs text-slate-500">{details.summary}</span>}
        </span>
        <span className="shrink-0 text-xs font-semibold" style={{ color: st.color }}>{st.label}</span>
        <span className={`shrink-0 text-lg leading-none text-brand-400 transition-transform duration-200 ${open ? 'rotate-90' : ''}`} aria-hidden="true">›</span>
      </button>

      {open && (
        <div className="border-t border-slate-800 px-3 py-3 sm:px-4">
          {loading && (
            <div className="flex items-center gap-2 py-3 text-sm text-slate-400">
              <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-brand-400" aria-hidden="true" />
              Carregando detalhes…
            </div>
          )}
          {error && <p className="py-2 text-sm text-red-400">{error}</p>}
          {details && !loading && (
            <Body details={details} tipo={tipo} busy={busy} onDismiss={dismiss} onAction={runAction} />
          )}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ corpo por tipo --- //

function Row({ label, children }) {
  return (
    <div className="flex justify-between gap-3 py-1 text-sm">
      <span className="text-slate-500">{label}</span>
      <span className="text-right font-medium text-slate-200">{children ?? '—'}</span>
    </div>
  );
}

function Body({ details, tipo, busy, onDismiss, onAction }) {
  return (
    <div className="space-y-3">
      <TypeData details={details} tipo={tipo} busy={busy} onDismiss={onDismiss} />

      {details.guidance && (
        <div className="rounded-lg bg-slate-900/60 px-3 py-2.5 text-sm text-slate-300">
          <span className="mr-1" aria-hidden="true">💡</span>
          <span className="whitespace-pre-line">{details.guidance}</span>
        </div>
      )}

      {Array.isArray(details.actions) && details.actions.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {details.actions.map((a, i) => (
            a.type === 'link' ? (
              <a key={i} href={a.url} target={a.target || '_blank'} rel="noreferrer noopener"
                onClick={() => onAction(a)}
                className="inline-flex min-h-[40px] items-center rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800">
                {a.label}
              </a>
            ) : (
              <button key={i} type="button" disabled={busy} onClick={() => onAction(a)}
                className="inline-flex min-h-[40px] items-center rounded-lg border border-slate-700 px-3 py-2 text-xs font-semibold text-slate-200 hover:bg-slate-800 disabled:opacity-50">
                {a.label}
              </button>
            )
          ))}
        </div>
      )}

      {Array.isArray(details.history) && details.history.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-semibold text-slate-400 hover:text-slate-200">
            Histórico ({details.history.length})
          </summary>
          <ul className="mt-2 space-y-1">
            {details.history.map((h, i) => (
              <li key={i} className="flex justify-between gap-3 text-xs text-slate-400">
                <span className="truncate">{h.title || '—'}</span>
                <span className="shrink-0 text-slate-500">{fmtDate(h.created_at)}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function TypeData({ details, tipo, busy, onDismiss }) {
  const d = details.data || {};
  if (tipo === 'phishing') {
    const pend = pendingAlerts(details);
    if (pend.length === 0) {
      return <p className="text-sm text-slate-400">Nenhum domínio suspeito no momento.</p>;
    }
    return (
      <ol className="space-y-2">
        {pend.map((a) => (
          <li key={a.id} className="rounded-lg border border-slate-800 bg-slate-900/40 p-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="truncate font-mono text-sm text-slate-100">{a.suspicious_domain}</span>
              <span className="shrink-0 text-xs text-slate-500">{fmtDate(a.detected_at)}</span>
            </div>
            <p className="mt-0.5 text-xs text-slate-500">
              {a.similarity_label}{a.distance != null ? ` · distância ${a.distance}` : ''}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <a href={`https://${a.suspicious_domain}`} target="_blank" rel="noreferrer noopener"
                className="inline-flex min-h-[36px] items-center rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs font-semibold text-slate-200 hover:bg-slate-800">
                Verificar site ↗
              </a>
              <button type="button" disabled={busy} onClick={() => onDismiss(a.id)}
                className="inline-flex min-h-[36px] items-center rounded-lg border border-slate-700 px-2.5 py-1.5 text-xs font-semibold text-slate-400 hover:bg-slate-800 disabled:opacity-50">
                Não é ameaça
              </button>
            </div>
          </li>
        ))}
      </ol>
    );
  }

  if (tipo === 'ssl') {
    return (
      <div>
        <Row label="Emissor">{d.issuer}</Row>
        <Row label="Válido até">{d.valid_until ? fmtDate(d.valid_until) : '—'}</Row>
        <Row label="Dias restantes">{d.days_left != null ? `${d.days_left} dias` : '—'}</Row>
      </div>
    );
  }

  if (tipo === 'domain') {
    return (
      <div>
        <Row label="Expira em">{d.expiry_date ? fmtDate(d.expiry_date) : '—'}</Row>
        <Row label="Dias restantes">{d.days_left != null ? `${d.days_left} dias` : '—'}</Row>
      </div>
    );
  }

  if (tipo === 'score') {
    const changed = Array.isArray(d.checks_changed) ? d.checks_changed : [];
    return (
      <div>
        <Row label="Nota atual">{d.current_score != null ? `${d.current_score}/100` : '—'}</Row>
        {d.previous_score != null && <Row label="Nota anterior">{d.previous_score}/100</Row>}
        {d.delta != null && d.delta !== 0 && (
          <Row label="Variação">
            <span style={{ color: d.delta > 0 ? '#22c55e' : '#ef4444' }}>
              {d.delta > 0 ? '+' : ''}{d.delta}
            </span>
          </Row>
        )}
        {changed.length > 0 && (
          <ul className="mt-2 space-y-1">
            {changed.map((c, i) => (
              <li key={i} className="flex items-center gap-2 text-sm">
                <span aria-hidden="true">{c.to === 'FAIL' ? '🔻' : '🔺'}</span>
                <span className="text-slate-300">{c.name}</span>
                <span className="text-xs text-slate-500">
                  {c.from === 'PASS' ? 'deixou de proteger' : 'voltou a proteger'}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  if (tipo === 'email') {
    const items = [['SPF', d.spf?.status], ['DKIM', d.dkim?.status], ['DMARC', d.dmarc?.status]];
    return (
      <div>
        {items.map(([name, state]) => {
          const m = emailStateLabel(state);
          return <Row key={name} label={name}><span aria-hidden="true">{m.icon}</span> {m.text}</Row>;
        })}
      </div>
    );
  }

  if (tipo === 'reputation') {
    const list = Array.isArray(d.blacklisted) ? d.blacklisted : [];
    if (list.length === 0) return <p className="text-sm text-slate-400">✅ Nenhuma blacklist detectada.</p>;
    return (
      <div>
        <p className="text-sm font-semibold text-red-400">Listado em:</p>
        <ul className="mt-1 list-inside list-disc text-sm text-slate-300">
          {list.map((b, i) => <li key={i}>{b}</li>)}
        </ul>
      </div>
    );
  }

  if (tipo === 'uptime') {
    return (
      <div>
        <Row label="Situação">{d.status === 'offline' ? 'Fora do ar' : 'No ar'}</Row>
        {d.last_response_code != null && <Row label="Último código HTTP">{d.last_response_code}</Row>}
        {d.last_response_time_ms != null && <Row label="Tempo de resposta">{d.last_response_time_ms} ms</Row>}
        {d.consecutive_failures > 0 && <Row label="Falhas seguidas">{d.consecutive_failures}</Row>}
      </div>
    );
  }

  if (tipo === 'changes') {
    const snap = d.last_snapshot || {};
    if (!snap.taken_at) return <p className="text-sm text-slate-400">Aguardando a primeira leitura do site.</p>;
    return (
      <div>
        {snap.title && <Row label="Título da página">{snap.title}</Row>}
        <Row label="Última leitura">{fmtDate(snap.taken_at)}</Row>
      </div>
    );
  }

  return null;
}
