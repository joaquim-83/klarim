// KL-83 Prompt 2 — testes da lógica pura (runner nativo `node --test`, sem deps).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  sortRows, paginate, journeyStepKind, STEP_COLOR, bounceColor, clickRateColor,
  deltaMeta, filterSectors, escapeHtml, parseTabHash, buildTabHash,
  DATA_SOURCE, dailySeriesToTrend, sparkFromDaily, serverFunnelStages,
  retentionBars, heatColor, DOW_LABELS,
} from './analyticsUtils.js'

test('sortRows: numérico desc (default)', () => {
  const r = sortRows([{ v: 3 }, { v: 1 }, { v: 2 }], 'v')
  assert.deepEqual(r.map((x) => x.v), [3, 2, 1])
})

test('sortRows: numérico asc', () => {
  const r = sortRows([{ v: 3 }, { v: 1 }, { v: 2 }], 'v', 'asc')
  assert.deepEqual(r.map((x) => x.v), [1, 2, 3])
})

test('sortRows: string via localeCompare', () => {
  const r = sortRows([{ p: '/c' }, { p: '/a' }, { p: '/b' }], 'p', 'asc')
  assert.deepEqual(r.map((x) => x.p), ['/a', '/b', '/c'])
})

test('sortRows: null sempre por último (asc e desc)', () => {
  assert.equal(sortRows([{ v: 1 }, { v: null }, { v: 2 }], 'v', 'asc').at(-1).v, null)
  assert.equal(sortRows([{ v: 1 }, { v: null }, { v: 2 }], 'v', 'desc').at(-1).v, null)
})

test('sortRows: não muta o array original', () => {
  const orig = [{ v: 1 }, { v: 2 }]
  sortRows(orig, 'v')
  assert.deepEqual(orig.map((x) => x.v), [1, 2])
})

test('paginate: fatia + total + pages', () => {
  const r = paginate([1, 2, 3, 4, 5], 1, 2)
  assert.deepEqual(r.slice, [1, 2])
  assert.equal(r.total, 5)
  assert.equal(r.pages, 3)
})

test('paginate: clampa page ao intervalo válido', () => {
  assert.equal(paginate([1, 2, 3], 99, 2).page, 2)
  assert.equal(paginate([1, 2, 3], 0, 2).page, 1)
})

test('paginate: vazio → 1 página', () => {
  const r = paginate([], 1, 25)
  assert.equal(r.total, 0)
  assert.equal(r.pages, 1)
  assert.deepEqual(r.slice, [])
})

test('journeyStepKind: entry/exit/conversion/normal', () => {
  assert.equal(journeyStepKind('alerta'), 'entry')
  assert.equal(journeyStepKind('[saiu]'), 'exit')
  assert.equal(journeyStepKind('/cadastrar'), 'conversion')
  assert.equal(journeyStepKind('/scan?url=x'), 'conversion')
  assert.equal(journeyStepKind('/site/{domain}'), 'normal')
  assert.ok(STEP_COLOR.entry && STEP_COLOR.exit && STEP_COLOR.conversion && STEP_COLOR.normal)
})

test('bounceColor: thresholds 70/50', () => {
  assert.equal(bounceColor(72), '#F85149')
  assert.equal(bounceColor(55), '#F0C000')
  assert.equal(bounceColor(20), '#00D26A')
})

test('clickRateColor: thresholds 15/8', () => {
  assert.equal(clickRateColor(21.4), '#00D26A')
  assert.equal(clickRateColor(10), '#F0C000')
  assert.equal(clickRateColor(3), '#F85149')
})

test('deltaMeta: +verde / -vermelho / — cinza', () => {
  assert.equal(deltaMeta(12).text, '+12')
  assert.equal(deltaMeta(12).color, '#00D26A')
  assert.equal(deltaMeta(-3).text, '-3')
  assert.equal(deltaMeta(-3).color, '#F85149')
  assert.equal(deltaMeta(0).text, '—')
})

test('filterSectors: dropa 0 alertas e limita a N', () => {
  const secs = [
    { sector: 'a', alerts_sent: 10 }, { sector: 'b', alerts_sent: 0 },
    { sector: 'c', alerts_sent: 5 }, { sector: 'd', alerts_sent: 3 },
  ]
  const { shown, hidden } = filterSectors(secs, 2)
  assert.deepEqual(shown.map((s) => s.sector), ['a', 'c'])   // 'b' dropado (0), cap 2
  assert.equal(hidden, 1)                                     // 'd' escondido
})

test('escapeHtml: escapa <, >, &, aspas', () => {
  assert.equal(escapeHtml('<b>&"x\'</b>'), '&lt;b&gt;&amp;&quot;x&#39;&lt;/b&gt;')
})

test('parseTabHash + buildTabHash: round-trip com params', () => {
  const tabs = ['overview', 'events', 'pages', 'journeys']
  const p = parseTabHash('#events?path=/site/x&group=session', tabs)
  assert.equal(p.tab, 'events')
  assert.equal(p.params.path, '/site/x')
  assert.equal(p.params.group, 'session')
  assert.equal(parseTabHash('#bogus', tabs).tab, 'overview')   // inválido → 1ª aba
  assert.equal(buildTabHash('events', { path: '/x' }), '#events?path=%2Fx')
  assert.equal(buildTabHash('overview', {}), '#overview')
})

// --- KL-92 Prompt 2 — dashboard server-side ------------------------------- #

test('DATA_SOURCE: server e tracker têm ícone e label', () => {
  assert.equal(DATA_SOURCE.server.icon, '📡')
  assert.equal(DATA_SOURCE.tracker.icon, '📱')
  assert.ok(DATA_SOURCE.server.title.length > 0)
})

test('dailySeriesToTrend: alinha dates com as séries', () => {
  const rows = dailySeriesToTrend({ dates: ['2026-07-15', '2026-07-16'], visitors_br: [45, 52], scans: [12, 8], accounts: [0, 1] })
  assert.deepEqual(rows[0], { date: '2026-07-15', visitors_br: 45, scans: 12, accounts: 0 })
  assert.equal(rows[1].accounts, 1)
})

test('dailySeriesToTrend: séries ausentes → 0', () => {
  const rows = dailySeriesToTrend({ dates: ['2026-07-15'] })
  assert.deepEqual(rows[0], { date: '2026-07-15', visitors_br: 0, scans: 0, accounts: 0 })
})

test('dailySeriesToTrend: entrada vazia → []', () => {
  assert.deepEqual(dailySeriesToTrend(null), [])
  assert.deepEqual(dailySeriesToTrend({}), [])
})

test('sparkFromDaily: extrai o array da chave', () => {
  assert.deepEqual(sparkFromDaily({ visitors_br: [1, 2, 3] }, 'visitors_br'), [1, 2, 3])
  assert.deepEqual(sparkFromDaily(null, 'scans'), [])
})

test('serverFunnelStages: ordem + taxas inter-etapa', () => {
  const stages = serverFunnelStages({
    visitors_br: 80, viewed_profile: 45, started_scan: 12, completed_scan: 10,
    created_account: 5, downloaded_pdf: 3,
    conversion_rates: { visit_to_profile: 56.3, profile_to_scan: 26.7, scan_to_account: 50.0, account_to_pdf: 60.0, overall: 6.3 },
  })
  assert.equal(stages.length, 6)
  assert.equal(stages[0].key, 'visitors_br')
  assert.equal(stages[0].rate, null)                 // 1ª etapa sem taxa
  assert.equal(stages[1].rate, 56.3)                 // viewed_profile ← visit_to_profile
  assert.equal(stages[4].total, 5)                   // created_account
  assert.equal(stages[4].rate, 50.0)                 // ← scan_to_account
})

test('serverFunnelStages: funnel vazio → totais 0', () => {
  const stages = serverFunnelStages(undefined)
  assert.equal(stages.length, 6)
  assert.equal(stages[0].total, 0)
  assert.equal(stages[1].rate, null)
})

test('retentionBars: D1/D3/D7 com pct/returned/total', () => {
  const bars = retentionBars({ day_1: { returned: 3, total: 5, pct: 60 }, day_3: { returned: 2, total: 5, pct: 40 }, day_7: { returned: 1, total: 5, pct: 20 } })
  assert.deepEqual(bars.map((b) => b.label), ['D1', 'D3', 'D7'])
  assert.equal(bars[0].pct, 60)
  assert.equal(bars[2].returned, 1)
})

test('retentionBars: ausente → zeros', () => {
  const bars = retentionBars(null)
  assert.equal(bars.length, 3)
  assert.equal(bars[0].pct, 0)
  assert.equal(bars[0].total, 0)
})

test('heatColor: 0 ou max=0 → transparent; max → laranja opaco', () => {
  assert.equal(heatColor(0, 30), 'transparent')
  assert.equal(heatColor(5, 0), 'transparent')
  assert.equal(heatColor(30, 30), 'rgba(255,107,53,1.00)')
  assert.ok(heatColor(3, 30).startsWith('rgba(255,107,53,'))   // parcial
})

test('DOW_LABELS: 7 dias começando no domingo', () => {
  assert.equal(DOW_LABELS.length, 7)
  assert.equal(DOW_LABELS[0], 'Dom')
  assert.equal(DOW_LABELS[6], 'Sáb')
})
