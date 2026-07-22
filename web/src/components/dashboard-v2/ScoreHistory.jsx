// KL-90 P2/UX — ScoreHistory: gráfico de linha do histórico de score (SVG puro, CSP-safe).
// Fix visual: sem `h-full` (card = altura do conteúdo) + eixo Y auto-escalado ao intervalo dos
// dados (scores altos não "grudam" no topo deixando o resto vazio) + altura compacta.
import { useState } from 'react';
import { card, fmtDate } from './shared.js';

export default function ScoreHistory({ history }) {
  const [hover, setHover] = useState(null);
  const data = (history || []).filter((d) => d && d.score != null);
  return (
    <div className={card}>
      <h3 className="text-lg font-bold text-white">📈 Evolução do score</h3>
      {data.length <= 1 ? (
        <div className="mt-3 flex h-24 flex-col items-center justify-center text-center text-sm text-slate-500">
          <span className="text-3xl font-bold text-slate-300">{data[0]?.score ?? '—'}</span>
          <p className="mt-1">O gráfico será preenchido com os próximos scans.</p>
        </div>
      ) : (
        <Chart data={data} hover={hover} setHover={setHover} />
      )}
    </div>
  );
}

function Chart({ data, hover, setHover }) {
  const w = 100, h = 32;
  const scores = data.map((d) => d.score);
  // Eixo Y auto-escalado ao intervalo (com folga), preso a [0,100].
  const lo = Math.max(0, Math.min(...scores) - 4);
  const hi = Math.min(100, Math.max(...scores) + 4);
  const span = Math.max(hi - lo, 1);
  const pts = data.map((d, i) => ({
    x: (i / Math.max(data.length - 1, 1)) * w,
    y: h - ((d.score - lo) / span) * h,
    d,
  }));
  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
  const area = `${path} L${w},${h} L0,${h} Z`;
  const first = data[0], last = data[data.length - 1];
  const cur = hover != null ? pts[hover] : null;
  return (
    <div className="mt-3">
      <div className="relative h-28">
        <svg viewBox={`0 0 ${w} ${h}`} className="h-full w-full" preserveAspectRatio="none">
          <path d={area} fill="#f97316" opacity="0.08" />
          <path d={path} fill="none" stroke="#f97316" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
          {pts.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r={hover === i ? 2.4 : 1.4} fill="#f97316"
              onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
              style={{ cursor: 'pointer' }} />
          ))}
        </svg>
        {cur && (
          <div className="pointer-events-none absolute -translate-x-1/2 rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-100 shadow-lg"
            style={{ left: `${cur.x}%`, top: `${(cur.y / h) * 100}%`, transform: 'translate(-50%, -120%)' }}>
            {fmtDate(cur.d.date)} · <span className="font-bold text-white">{cur.d.score}</span>
          </div>
        )}
      </div>
      <div className="mt-2 flex justify-between text-xs text-slate-500">
        <span>{fmtDate(first.date)} · {first.score}</span>
        <span>{fmtDate(last.date)} · {last.score}</span>
      </div>
    </div>
  );
}
