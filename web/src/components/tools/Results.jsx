// KL-134 P2 — renderizadores de resultado das 5 ferramentas. Componentes React puros (recebem
// `data` do endpoint e desenham). Usados dentro da ilha ToolPage (client:load). Cores de
// score/status via style inline (constantes nos 2 temas); o resto usa utilitários slate/white
// theme-aware (KL-87).
import { formatScore, gradeColor, lgpdGradeColor, statusMeta, groupTechByCategory } from '../../lib/tools.js';

const card = 'rounded-2xl border border-slate-800 bg-slate-900/60 p-5 sm:p-6';

function StatusRow({ status, name, children }) {
  const s = statusMeta(status);
  return (
    <li class="flex items-start gap-3 border-t border-slate-800 py-3 first:border-t-0">
      <span class="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-sm font-bold"
        style={{ color: s.color, background: `${s.color}22` }} aria-hidden="true">{s.icon}</span>
      <div class="min-w-0">
        <div class="font-medium text-white">{name}</div>
        {children}
      </div>
    </li>
  );
}

function ContextBox({ context }) {
  if (!context) return null;
  const stats = context.stats || (context.stat ? [context.stat] : []);
  if (!stats.length) return null;
  return (
    <div class="mt-5 rounded-xl border border-brand-500/30 bg-brand-500/10 p-4 text-sm">
      <p class="font-semibold text-brand-300">📊 Você sabia?</p>
      <ul class="mt-1 space-y-1 text-slate-300">
        {stats.map((s, i) => <li key={i}>{s}</li>)}
      </ul>
      {context.source && <p class="mt-2 text-xs text-slate-500">Fonte: {context.source}</p>}
    </div>
  );
}

function ScoreBadge({ score, sub }) {
  const f = formatScore(score);
  return (
    <div class="flex items-center gap-3">
      <span class="inline-flex h-14 min-w-14 items-center justify-center rounded-2xl px-3 text-2xl font-extrabold"
        style={{ color: f.color, background: `${f.color}1f` }}>{f.text}</span>
      {sub && <span class="text-sm text-slate-400">{sub}</span>}
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function SslResult({ data }) {
  const invalid = data.valid === false;
  return (
    <div class={card}>
      <div class="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p class="text-sm text-slate-400">Certificado SSL de</p>
          <p class="text-lg font-bold text-white">{data.domain}</p>
        </div>
        {data.grade ? (
          <span class="inline-flex h-16 w-16 items-center justify-center rounded-2xl text-3xl font-extrabold"
            style={{ color: gradeColor(data.grade), background: `${gradeColor(data.grade)}1f` }}
            aria-label={`Nota ${data.grade}`}>{data.grade}</span>
        ) : (
          <span class="rounded-lg bg-red-500/15 px-3 py-1 text-sm font-semibold text-red-400">Inválido</span>
        )}
      </div>

      {invalid && data.error && (
        <p class="mt-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-300">⚠️ {data.error}</p>
      )}

      {!invalid && (
        <dl class="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 text-sm sm:grid-cols-4">
          <div><dt class="text-slate-500">Válido</dt><dd class="font-medium text-white">{data.valid ? 'Sim' : 'Não'}</dd></div>
          <div><dt class="text-slate-500">Dias restantes</dt><dd class="font-medium text-white">{data.days_remaining ?? '—'}</dd></div>
          <div><dt class="text-slate-500">Emissor</dt><dd class="font-medium text-white">{data.issuer || '—'}</dd></div>
          <div><dt class="text-slate-500">Protocolo</dt><dd class="font-medium text-white">{data.protocol || '—'}</dd></div>
        </dl>
      )}

      {Array.isArray(data.checks) && data.checks.length > 0 && (
        <ul class="mt-4">
          {data.checks.map((c, i) => (
            <StatusRow key={i} status={c.status} name={c.name}>
              {c.detail && <p class="text-sm text-slate-400">{c.detail}</p>}
            </StatusRow>
          ))}
        </ul>
      )}
      <ContextBox context={data.context} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
const IMPORTANCE_COLOR = { alta: '#ef4444', média: '#eab308', media: '#eab308', baixa: '#94a3b8' };

export function HeadersResult({ data }) {
  return (
    <div class={card}>
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-sm text-slate-400">Headers de segurança de</p>
          <p class="text-lg font-bold text-white">{data.domain}</p>
        </div>
        <ScoreBadge score={data.score} sub="presentes" />
      </div>
      <ul class="mt-4">
        {(data.headers || []).map((h, i) => (
          <StatusRow key={i} status={h.present ? 'pass' : 'fail'} name={
            <span class="flex flex-wrap items-center gap-2">
              <span>{h.name}</span>
              <span class="rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide"
                style={{ color: IMPORTANCE_COLOR[h.importance] || '#94a3b8', background: `${IMPORTANCE_COLOR[h.importance] || '#94a3b8'}22` }}>
                {h.importance}
              </span>
            </span>
          }>
            <p class="text-sm text-slate-400">{h.explanation}</p>
            {h.present && h.value && <p class="mt-1 break-all font-mono text-xs text-slate-500">{h.value}</p>}
          </StatusRow>
        ))}
      </ul>
      <ContextBox context={data.context} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
const LGPD_DISCLAIMER =
  'Esta verificação avalia indicadores técnicos detectáveis automaticamente. Não substitui uma ' +
  'auditoria jurídica de conformidade à LGPD.';

export function LgpdResult({ data }) {
  const gc = lgpdGradeColor(data.grade);
  return (
    <div class={card}>
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p class="text-sm text-slate-400">Indicadores de LGPD de</p>
          <p class="text-lg font-bold text-white">{data.domain}</p>
        </div>
        <div class="flex items-center gap-3">
          <ScoreBadge score={data.score} />
          {data.grade && (
            <span class="rounded-lg px-3 py-1 text-sm font-semibold"
              style={{ color: gc, background: `${gc}1f` }}>{data.grade}</span>
          )}
        </div>
      </div>
      <ul class="mt-4">
        {(data.indicators || []).map((it, i) => (
          <StatusRow key={i} status={it.status} name={it.name}>
            {it.explanation && <p class="text-sm text-slate-400">{it.explanation}</p>}
          </StatusRow>
        ))}
      </ul>
      <p class="mt-4 rounded-xl border border-slate-700 bg-slate-800/40 p-3 text-xs leading-relaxed text-slate-400">
        ⚖️ {LGPD_DISCLAIMER}
      </p>
      <ContextBox context={data.context} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function TechResult({ data }) {
  const groups = groupTechByCategory(data.technologies);
  return (
    <div class={card}>
      <p class="text-sm text-slate-400">Tecnologias detectadas em</p>
      <p class="text-lg font-bold text-white">{data.domain}</p>

      {groups.length === 0 ? (
        <p class="mt-4 text-slate-400">{data.message || 'Nenhuma tecnologia identificada.'}</p>
      ) : (
        <div class="mt-4 space-y-4">
          {groups.map((g, i) => (
            <div key={i}>
              <h3 class="text-xs font-bold uppercase tracking-wide text-slate-500">{g.category}</h3>
              <ul class="mt-2 flex flex-wrap gap-2">
                {g.items.map((t, j) => (
                  <li key={j} class="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm text-white">
                    {t.name}{t.version ? <span class="text-slate-400"> {t.version}</span> : null}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
      <ContextBox context={data.context} />
    </div>
  );
}

// --------------------------------------------------------------------------- //
export function EmailResult({ data }) {
  return (
    <div class={card}>
      <div class="flex items-center justify-between gap-3">
        <div>
          <p class="text-sm text-slate-400">Proteção de email de</p>
          <p class="text-lg font-bold text-white">{data.domain}</p>
        </div>
        <ScoreBadge score={data.score} sub="corretos" />
      </div>
      <ul class="mt-4">
        {(data.records || []).map((r, i) => (
          <StatusRow key={i} status={r.status} name={r.name}>
            {r.explanation && <p class="text-sm text-slate-400">{r.explanation}</p>}
            {r.detail && <p class="mt-1 text-sm text-slate-300">{r.detail}</p>}
            {r.value && (
              <p class="mt-1 break-all font-mono text-xs text-slate-500">
                {Array.isArray(r.value) ? r.value.join(', ') : r.value}
              </p>
            )}
            {r.recommendation && (
              <p class="mt-2 rounded-lg border border-brand-500/30 bg-brand-500/10 p-2 text-xs text-brand-200">
                💡 {r.recommendation}
              </p>
            )}
          </StatusRow>
        ))}
      </ul>
    </div>
  );
}

// Dispatcher por slug — a ilha ToolPage passa o `tool` e o `data`.
const MAP = { ssl: SslResult, headers: HeadersResult, lgpd: LgpdResult, tech: TechResult, email: EmailResult };

export default function ToolResult({ tool, data }) {
  const Comp = MAP[tool];
  if (!Comp || !data) return null;
  return <Comp data={data} />;
}
