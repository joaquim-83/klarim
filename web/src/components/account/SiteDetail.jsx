import { useEffect, useState } from 'react';
import { apiGet } from '../../lib/api.js';
import { card } from './ui.js';
import { groupByCategory } from '../scan/checks.js';
import ShareScore from './ShareScore.jsx';
import OwnershipVerification from './OwnershipVerification';
import TechnicianSection from './TechnicianSection';

const SEMA = {
  verde: { dot: '🟢', ring: 'ring-green-500/50', text: 'text-green-400' },
  amarelo: { dot: '🟡', ring: 'ring-yellow-500/50', text: 'text-yellow-400' },
  vermelho: { dot: '🔴', ring: 'ring-red-500/50', text: 'text-red-400' },
};

function fmtDate(s) {
  if (!s) return '—';
  try { return new Date(s).toLocaleDateString('pt-BR'); } catch { return '—'; }
}

// Gráfico de evolução leve (SVG inline, sem dependência) — score x tempo.
function EvolutionChart({ history }) {
  const pts = history.filter((h) => h.score != null);
  if (pts.length < 2) return <p className="text-sm text-slate-400">Ainda não há histórico suficiente para o gráfico. A evolução aparece a partir do 2º scan.</p>;
  const W = 520, H = 120, pad = 24;
  const xs = (i) => pad + (i * (W - 2 * pad)) / (pts.length - 1);
  const ys = (v) => H - pad - (v / 100) * (H - 2 * pad);
  const line = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${xs(i).toFixed(1)},${ys(p.score).toFixed(1)}`).join(' ');
  const first = pts[0].score, last = pts[pts.length - 1].score;
  const delta = last - first;
  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="Evolução do score">
        <line x1={pad} y1={ys(50)} x2={W - pad} y2={ys(50)} stroke="#30363D" strokeDasharray="4 4" />
        <path d={line} fill="none" stroke="#FF6B35" strokeWidth="2.5" />
        {pts.map((p, i) => <circle key={i} cx={xs(i)} cy={ys(p.score)} r="3.5" fill="#FF6B35" />)}
      </svg>
      <p className="mt-2 text-sm text-slate-400">
        {fmtDate(pts[0].scanned_at)}: {first} → {fmtDate(pts[pts.length - 1].scanned_at)}: {last}{' '}
        <span className={delta > 0 ? 'text-green-400' : delta < 0 ? 'text-red-400' : 'text-slate-400'}>
          ({delta > 0 ? `↑ +${delta}` : delta < 0 ? `↓ ${delta}` : '→ estável'})
        </span>
      </p>
    </div>
  );
}

// KL-68 — seção de propriedade no detalhe do site (dashboard). Busca o status e mostra
// o selo (dono) ou o fluxo de verificação por código (se disponível).
function OwnershipSection({ targetId }) {
  const [st, setSt] = useState(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    apiGet(`/account/ownership/status?target_id=${targetId}`).then(({ ok, data }) => { if (ok) setSt(data); });
  }, [targetId]);
  if (!st) return null;
  if (st.is_owner) {
    return (
      <div className={`${card} border-green-500/30 bg-green-500/5`}>
        <p className="text-sm font-semibold text-green-400">✓ Você é o dono verificado deste site.</p>
      </div>
    );
  }
  // KL-71 Bug 3: site já tem outro dono verificado (first-come) — explica ao usuário.
  if (st.has_other_owner) {
    return (
      <div className={`${card} border-slate-700 bg-slate-800/40`}>
        <p className="text-sm font-semibold text-slate-200">ℹ️ Este site já tem um dono verificado.</p>
        <p className="mt-1 text-sm text-slate-400">
          Você pode continuar monitorando o score. Se você é o proprietário legítimo, entre em
          contato com <a href="mailto:scan@klarim.net" className="text-brand-400 hover:text-brand-300">scan@klarim.net</a>.
        </p>
      </div>
    );
  }
  if (!st.verification_available) return null;
  return (
    <div className={`${card} border-brand-500/30 bg-brand-500/5`}>
      <p className="text-sm font-semibold text-white">Confirme que este site é seu</p>
      <p className="mt-1 text-sm text-slate-400">Verifique a propriedade para ganhar o selo de dono verificado.</p>
      <div className="mt-3">
        {open
          ? <OwnershipVerification targetId={targetId} onVerified={() => setSt({ ...st, is_owner: true })} />
          : <button onClick={() => setOpen(true)}
              className="rounded-lg border border-brand-500/40 bg-brand-500/10 px-4 py-2 text-sm font-semibold text-brand-300 hover:bg-brand-500/20">
              Verificar propriedade →
            </button>}
      </div>
    </div>
  );
}

// KL-44 P4 — status do monitoramento contínuo (vigílias) do site, com destaque para o
// uptime (disponibilidade). Só aparece se a conta tiver vigílias ativas neste domínio.
const VIG_LABEL = {
  ssl: 'Certificado SSL', domain: 'Registro do domínio', score: 'Score de segurança',
  email: 'Proteção de e-mail', reputation: 'Reputação', uptime: 'Disponibilidade',
  changes: 'Integridade do site', phishing: 'Domínios suspeitos',
};

function MonitoringSection({ domain }) {
  const [vigs, setVigs] = useState(null);
  useEffect(() => {
    if (!domain) return;
    apiGet('/account/vigilias').then(({ ok, data }) => {
      if (ok) setVigs((data.vigilias || []).filter((v) => v.site_domain === domain));
    });
  }, [domain]);
  if (!vigs || vigs.length === 0) return null;
  const uptime = vigs.find((v) => v.tipo === 'uptime');
  const ud = (uptime && uptime.last_data) || null;
  const isDown = ud && ud.down_since;
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Monitoramento contínuo</h2>
      {uptime && (
        <div className={`mt-3 rounded-lg border px-4 py-3 ${isDown ? 'border-red-500/40 bg-red-500/5' : 'border-green-500/30 bg-green-500/5'}`}>
          <p className="text-sm font-semibold">
            {isDown ? '🔴 Site fora do ar' : '🟢 Site no ar'}
            {!isDown && ud && ud.last_response_time_ms != null && (
              <span className="ml-2 font-normal text-slate-400">tempo de resposta {ud.last_response_time_ms}ms</span>
            )}
          </p>
          {isDown && <p className="mt-1 text-xs text-red-300">Fora do ar desde {fmtDate(ud.down_since)}. Continuaremos monitorando.</p>}
        </div>
      )}
      <ul className="mt-3 flex flex-wrap gap-2">
        {vigs.map((v) => (
          <li key={v.tipo} className="rounded-full border border-slate-700 bg-slate-800/60 px-3 py-1 text-xs text-slate-300">
            {v.last_status === 'critical' ? '🔴' : v.last_status === 'warning' ? '🟡' : '🟢'} {VIG_LABEL[v.tipo] || v.tipo}
          </li>
        ))}
      </ul>
    </div>
  );
}

// KL-44 P5 — recomendação genérica por indicador (NÃO é assessoria jurídica).
const PRIVACY_TODO = {
  privacy_policy: 'Publique uma página de Política de Privacidade e linke-a no rodapé.',
  cookie_consent: 'Instale um banner de consentimento de cookies (CookieYes, OneTrust, Cookiebot, etc.).',
  third_party_cookies: 'Carregue scripts de rastreio (Analytics, Meta, Ads) só após o consentimento.',
  dsar_channel: 'Ofereça um canal para o titular exercer seus direitos (acesso, correção, exclusão).',
  dpo_info: 'Identifique o Encarregado (DPO) e um contato na política de privacidade.',
  cookie_policy: 'Crie uma Política de Cookies dedicada (além da política de privacidade).',
  https_forms: 'Sirva todo o site (e os formulários) sobre HTTPS.',
  form_security_headers: 'Ative HSTS, CSP e X-Content-Type-Options nas páginas com formulários.',
};
const PRIVACY_DISCLAIMER = 'Este é um diagnóstico técnico automatizado baseado em verificações passivas. Não constitui assessoria jurídica e não substitui a avaliação de um advogado ou Encarregado de Proteção de Dados (DPO). Para conformidade completa com a LGPD, consulte um profissional qualificado.';

function PrivacySection({ privacy }) {
  if (!privacy || !Array.isArray(privacy.checks)) return null;
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Indicadores de privacidade: {privacy.score}/{privacy.total}</h2>
      <p className="mt-1 text-sm text-slate-400">Fatos técnicos observáveis por varredura passiva. Referência LGPD por indicador.</p>
      <ul className="mt-4 space-y-2.5">
        {privacy.checks.map((c) => (
          <li key={c.id} className="text-sm">
            <div className="flex items-start gap-2">
              <span aria-hidden="true">{c.status === 'PASS' ? '✅' : '❌'}</span>
              <span className="text-slate-200">{c.name}</span>
              <span className="ml-auto shrink-0 text-xs text-slate-500">{c.lgpd_ref}</span>
            </div>
            {c.status === 'FAIL' && PRIVACY_TODO[c.id] && (
              <p className="ml-6 mt-0.5 text-xs text-slate-400"><span className="text-slate-500">O que fazer:</span> {PRIVACY_TODO[c.id]}</p>
            )}
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-slate-800 pt-3 text-xs leading-relaxed text-slate-500">⚖️ {PRIVACY_DISCLAIMER}</p>
    </div>
  );
}

// KL-44 P5 (Bloco 2C) — selo "Monitorado por Klarim". Só para dono verificado.
function SealSection({ domain }) {
  const [theme, setTheme] = useState('auto');
  const [size, setSize] = useState('compact');
  const [copied, setCopied] = useState(false);
  const snippet = `<!-- Selo Klarim - Monitorado -->
<div id="klarim-seal"></div>
<script src="https://klarim.net/seal/widget.js"
        data-domain="${domain}"${theme !== 'auto' ? `\n        data-theme="${theme}"` : ''}${size !== 'compact' ? `\n        data-size="${size}"` : ''}></script>`;
  function copy() {
    navigator.clipboard?.writeText(snippet);
    setCopied(true); setTimeout(() => setCopied(false), 2000);
  }
  return (
    <div className={card}>
      <h2 className="text-lg font-bold text-white">Selo de monitoramento</h2>
      <p className="mt-1 text-sm text-slate-400">Exiba "Monitorado por Klarim" no seu site. Sem rastreio de visitantes.</p>
      <div className="mt-4 flex flex-wrap gap-3 text-sm">
        <label className="flex items-center gap-1.5 text-slate-400">Tema
          <select value={theme} onChange={(e) => setTheme(e.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
            <option value="auto">Auto</option><option value="dark">Escuro</option><option value="light">Claro</option>
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-slate-400">Tamanho
          <select value={size} onChange={(e) => setSize(e.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
            <option value="compact">Compacto</option><option value="full">Completo</option>
          </select>
        </label>
      </div>
      <pre className="mt-3 overflow-x-auto rounded-lg border border-slate-800 bg-slate-950 p-3 text-xs text-slate-300"><code>{snippet}</code></pre>
      <button onClick={copy} className="mt-2 rounded-lg bg-brand-500 px-4 py-2 text-sm font-semibold text-slate-950 hover:bg-brand-400">
        {copied ? 'Copiado ✓' : 'Copiar snippet'}
      </button>
      <p className="mt-2 text-xs text-slate-500">Sugestão: cole no rodapé do site. O selo abre o perfil público em nova aba.</p>
    </div>
  );
}

export default function SiteDetail({ targetId }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState('');

  useEffect(() => {
    apiGet(`/account/sites/${targetId}`).then(({ ok, data, error }) => {
      if (ok) setData(data); else setErr(error || 'Não foi possível carregar.');
    });
  }, [targetId]);

  if (err) return <p className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-300">{err}</p>;
  if (!data) return <p className="text-slate-400">Carregando…</p>;

  const sema = SEMA[data.semaphore] || SEMA.amarelo;
  const groups = groupByCategory(data.checks || []);
  const t = data.target || {};
  const p = data.profile || {};
  const encoded = encodeURIComponent(t.url || '');

  return (
    <div className="space-y-8">
      <div>
        <a href="/dashboard" className="text-sm text-brand-400 hover:text-brand-300">← Voltar</a>
        <h1 className="mt-1 text-2xl font-bold text-white">{t.domain || t.url}</h1>
        {t.domain && (
          <a href={`/site/${t.domain}`} target="_blank" rel="noopener noreferrer"
            className="mt-1 inline-flex text-sm text-brand-400 hover:text-brand-300">
            Ver perfil público →
          </a>
        )}
      </div>

      {/* KL-68 — verificação de propriedade */}
      <OwnershipSection targetId={targetId} />

      {/* KL-44 P3 — técnico responsável + compartilhar laudo */}
      <TechnicianSection targetId={targetId} />

      {/* KL-44 P4 — monitoramento contínuo (vigílias + uptime) */}
      <MonitoringSection domain={t.domain} />

      {/* KL-44 P5 — indicadores de privacidade + selo (selo só p/ dono verificado) */}
      <PrivacySection privacy={data.privacy} />
      {data.is_owner && t.domain && <SealSection domain={t.domain} />}

      {/* Score */}
      <div className={`${card} text-center`}>
        <div className={`mx-auto flex h-32 w-32 flex-col items-center justify-center rounded-full ring-4 ${sema.ring}`}>
          <span className={`text-4xl font-extrabold ${sema.text}`}>{data.score ?? '—'}</span>
          <span className="text-sm text-slate-400">/100</span>
        </div>
        <p className="mt-3 text-xl">{sema.dot}</p>
        {data.badge && (
          <p className="mt-2 inline-flex items-center gap-1.5 rounded-full border border-brand-500/40 bg-brand-500/10 px-3 py-1 text-sm font-semibold text-brand-300">
            {data.badge.icon} {data.badge.label}
          </p>
        )}
        {data.ranking && data.ranking.total > 1 && (
          <p className="mt-3 text-sm text-slate-300">
            Posição: <span className="font-semibold text-white">#{data.ranking.position}</span> de {data.ranking.total} sites de {data.ranking.sector_label}
            <br />
            <span className="text-slate-400">Acima de {data.ranking.percentile}% do setor</span>{' '}
            <a href={`/ranking/${data.ranking.sector}`} className="text-brand-400 hover:text-brand-300">· ver ranking →</a>
          </p>
        )}
        <p className="mt-3 text-sm text-slate-400">Último scan: {fmtDate(t.last_scan_at)}</p>
      </div>

      {/* KL-20 — riscos em linguagem de negócio (não jargão) + benchmark do setor */}
      {data.risk_summary?.risks?.length > 0 && (
        <div className={`${card} border-yellow-500/30`}>
          <h2 className="text-lg font-bold text-white">Riscos para o seu negócio</h2>
          {data.benchmark_line && <p className="mt-1 text-sm text-slate-400">{data.benchmark_line}</p>}
          <ul className="mt-4 space-y-3">
            {data.risk_summary.risks.map((r) => (
              <li key={r.check_id} className="flex gap-3">
                <span className="shrink-0 text-lg">{r.icon || '⚠️'}</span>
                <div className="min-w-0">
                  <p className="text-sm font-semibold text-white">{r.headline}</p>
                  <p className="mt-0.5 break-words text-sm text-slate-300">{r.message}</p>
                </div>
              </li>
            ))}
          </ul>
          {data.risk_summary.remaining_count > 0 && (
            <p className="mt-3 text-sm text-slate-400">
              E mais {data.risk_summary.remaining_count} {data.risk_summary.remaining_count === 1 ? 'item' : 'itens'} nas verificações abaixo.
            </p>
          )}
        </div>
      )}

      {/* Evolução */}
      <div className={card}>
        <h2 className="text-lg font-bold text-white">Evolução</h2>
        <div className="mt-4"><EvolutionChart history={data.history || []} /></div>
      </div>

      {/* Perfil comercial */}
      {(p.description || p.business_type || (data.classifications || []).length > 0) && (
        <div className={card}>
          <h2 className="text-lg font-bold text-white">Perfil comercial</h2>
          <dl className="mt-3 space-y-2 text-sm">
            {p.company_name && <Row k="Nome" v={p.company_name} />}
            {p.business_type && <Row k="Tipo" v={p.business_type} />}
            {p.description && <Row k="Descrição" v={p.description} />}
            {(data.classifications || []).length > 0 && (
              <Row k="CNAE" v={data.classifications.slice(0, 3).map((c) => `${c.cnae_code} ${c.cnae_description || ''}`.trim()).join(' · ')} />
            )}
            {Array.isArray(p.tags) && p.tags.length > 0 && <Row k="Tags" v={p.tags.join(', ')} />}
            {p.maturity_score != null && <Row k="Maturidade digital" v={`${p.maturity_score}/10`} />}
          </dl>
        </div>
      )}

      {/* PDFs */}
      <div className="flex flex-wrap gap-3">
        <a href={`/api/report/executive?url=${encoded}`}
          className="rounded-xl bg-brand-500 px-5 py-3 text-sm font-semibold text-slate-950 hover:bg-brand-400">📄 PDF Executivo</a>
        <a href={`/api/report/technical?url=${encoded}`}
          className="rounded-xl border border-slate-700 px-5 py-3 text-sm text-slate-200 hover:bg-slate-800">📑 PDF Técnico</a>
        <a href="/dashboard/widget"
          className="rounded-xl border border-slate-700 px-5 py-3 text-sm text-slate-200 hover:bg-slate-800">&lt;/&gt; Widget para seu site</a>
      </div>

      {/* Compartilhar score (KL-42) */}
      {data.score != null && t.domain && (
        <ShareScore domain={t.domain} score={data.score} badge={data.badge} ranking={data.ranking} />
      )}

      {/* Checks */}
      <div>
        <h2 className="text-lg font-bold text-white">Verificações ({(data.checks || []).length})</h2>
        <div className="mt-4 space-y-4">
          {groups.map(([cat, list]) => {
            const ok = list.filter((c) => c.status === 'PASS').length;
            return (
              <div key={cat} className="rounded-2xl border border-slate-800 bg-slate-900/60 p-5">
                <p className="font-semibold text-white">{cat} <span className="text-sm font-normal text-slate-400">({ok}/{list.length} ✅)</span></p>
                <ul className="mt-3 space-y-1.5">
                  {list.map((c) => <CheckRow key={c.check_id} check={c} />)}
                </ul>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div className="flex flex-col gap-0.5 sm:flex-row sm:gap-3">
      <dt className="w-40 shrink-0 text-slate-500">{k}</dt>
      <dd className="text-slate-200">{v}</dd>
    </div>
  );
}

function CheckRow({ check }) {
  const [open, setOpen] = useState(false);
  const isFail = check.status === 'FAIL';
  const icon = isFail ? '❌' : check.status === 'PASS' ? '✅' : check.status === 'INCONCLUSO' ? '⚪' : '🔒';
  const canExpand = isFail && check.evidence;
  return (
    <li className="text-sm">
      <div className={`flex items-start gap-2 ${canExpand ? 'cursor-pointer' : ''}`} onClick={() => canExpand && setOpen((o) => !o)}>
        <span aria-hidden="true">{icon}</span>
        <span className={isFail ? 'text-slate-100' : 'text-slate-300'}>{check.name}</span>
        {canExpand && <span className="ml-auto text-xs text-slate-500">{open ? 'ocultar' : 'detalhes'}</span>}
      </div>
      {open && canExpand && (
        <div className="ml-6 mt-1.5 space-y-1.5 rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-slate-400">
          {check.evidence && <p><span className="text-slate-300">Evidência:</span> {check.evidence}</p>}
          {(check.owasp || check.cwe || check.lgpd) && (
            <p className="text-xs text-slate-500">{[check.owasp, check.cwe, check.lgpd].filter(Boolean).join(' · ')}</p>
          )}
        </div>
      )}
    </li>
  );
}
