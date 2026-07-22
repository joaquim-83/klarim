// KL-90 P2/UX (item 4) — card do score CONSOLIDADO: score+semáforo+tendência+benchmark,
// tira de status (SSL/online/último scan), ações (PDF/Compartilhar/Escanear), links de perfil
// público e landing, e o CTA destacado "Vincular Técnico". Substitui o StatusPanel separado.
import { card, brandBtn, outlineBtn, SEMA_COLOR, SEMA_DOT, SEMA_LABEL, relDate, profileUrl } from './shared.js';

function Trend({ trend, delta }) {
  if (trend === 'subindo') return <span className="text-sm font-semibold text-green-400">↑ +{delta}</span>;
  if (trend === 'caindo') return <span className="text-sm font-semibold text-red-400">↓ {delta}</span>;
  if (trend === 'primeiro') return <span className="text-sm text-slate-500">1º scan</span>;
  return <span className="text-sm text-slate-500">→ estável</span>;
}

export default function ScoreCard({ site, benchmark, scanning, onScan, onToast, onLinkTechnician, technician = false }) {
  const color = SEMA_COLOR[site.semaphore] || SEMA_COLOR.amarelo;
  const b = benchmark || {};
  // Modo técnico → PDF TÉCNICO (48 checks + evidência); dono → executivo.
  const kind = technician ? 'technical' : 'executive';
  const pdfLabel = technician ? '📄 Relatório técnico' : '📄 Relatório PDF';
  const pdfUrl = site.score != null ? `/api/report/${kind}?url=${encodeURIComponent(site.domain)}` : null;

  function share() {
    const url = profileUrl(site.domain);
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(url).then(() => onToast('🔗 Link copiado!'), () => onToast('Não foi possível copiar.'));
    } else onToast(url);
  }

  return (
    <div className={card}>
      {/* linha 1 — score + semáforo + tendência + benchmark */}
      <div className="flex flex-col gap-5 sm:flex-row sm:items-center">
        <div className="flex items-center gap-5">
          <div className="flex h-28 w-28 shrink-0 flex-col items-center justify-center rounded-full border-4"
            style={{ borderColor: color }}>
            <span className="text-4xl font-extrabold leading-none text-white">{site.score ?? '—'}</span>
            <span className="text-xs text-slate-400">/100</span>
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm text-slate-400">{site.domain}</p>
            <p className="mt-1 flex items-center gap-2 text-lg font-bold" style={{ color }}>
              <span aria-hidden="true">{SEMA_DOT[site.semaphore] || '⚪'}</span>{SEMA_LABEL[site.semaphore] || 'Atenção'}
            </p>
            <p className="mt-0.5"><Trend trend={site.trend} delta={site.trend_delta} /></p>
            {b.sector_label && (
              <p className="mt-1 text-xs text-slate-400">
                {b.sector_label}
                {b.rank_position && b.rank_total ? <> · {b.rank_position}º de {b.rank_total}</> : null}
                {b.sector_avg != null && (
                  <> · <span className={b.above_average ? 'text-green-400' : 'text-slate-400'}>
                    {b.above_average ? 'acima' : 'abaixo'} da média ({b.sector_avg})
                  </span></>
                )}
              </p>
            )}
          </div>
        </div>
      </div>

      {/* tira de status */}
      <div className="mt-4 flex flex-wrap gap-x-5 gap-y-1.5 border-t border-slate-800 pt-4 text-sm text-slate-300">
        <span className="flex items-center gap-1.5">{site.is_online ? '🟢' : '🔴'} {site.is_online ? 'Online' : 'Fora do ar'}</span>
        <span className="flex items-center gap-1.5">{site.ssl_days_remaining != null ? '🔒' : '❔'} {site.ssl_days_remaining != null ? `SSL ${site.ssl_days_remaining}d` : 'SSL —'}</span>
        <span className="flex items-center gap-1.5">🕐 Scan {relDate(site.last_scan_at)}</span>
      </div>

      {/* ações */}
      <div className="mt-4 flex flex-wrap gap-2">
        {pdfUrl
          ? <a href={pdfUrl} target="_blank" rel="noopener" className={brandBtn}>{pdfLabel}</a>
          : <button type="button" disabled className={brandBtn}>{pdfLabel}</button>}
        {!technician && <button type="button" onClick={share} className={outlineBtn}>↗ Compartilhar</button>}
        <button type="button" onClick={onScan} disabled={scanning} className={outlineBtn}>
          {scanning ? '⏳ Escaneando…' : '🔄 Escanear agora'}
        </button>
      </div>

      {/* link externo + (só dono) vincular técnico */}
      <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-slate-800 pt-4">
        <a href={`https://${site.domain}`} target="_blank" rel="noopener"
          className="inline-flex min-h-[44px] items-center text-sm text-brand-400 hover:text-brand-300">Ver landing page →</a>
        {!technician && (
          <button type="button" onClick={onLinkTechnician}
            className="ml-auto inline-flex min-h-[44px] items-center gap-2 rounded-xl border border-brand-500 bg-brand-500/10 px-4 text-sm font-semibold text-brand-300 hover:bg-brand-500/20">
            🔧 Vincular Técnico
          </button>
        )}
      </div>
    </div>
  );
}
