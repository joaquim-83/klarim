import { useState } from 'react'
import { admin } from '../../lib/admin/adminApi'
import { useAsync } from '../../lib/admin/useAsync'
import { Card, Loading, ErrorBox, Button, Badge, formatDate } from './ui'
import AdminShell from './AdminShell'

// KL-44 — Configurações editáveis ao vivo (banco > .env), gestão de senha e rotação do
// token MCP. Padrão da migração Astro: componente React usado como ilha (client:only).

function fmtUptime(s) {
  if (s == null) return '—'
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60)
  return [d ? `${d}d` : '', h ? `${h}h` : '', `${m}m`].filter(Boolean).join(' ')
}

// adminApi.req() LEVANTA em erro (mensagem "Erro {status}. {json}"); extrai o `detail`.
function errText(e) {
  const msg = (e && e.message) || 'Erro inesperado.'
  const i = msg.indexOf('{')
  if (i >= 0) { try { return JSON.parse(msg.slice(i)).detail || msg } catch { /* ignore */ } }
  return msg
}

export default function ConfigPage() {
  const cfg = useAsync(() => admin.configList(), [])
  const info = useAsync(() => admin.systemInfo(), [])
  const [msg, setMsg] = useState(null)   // { text, error }

  function flash(text, error = false) {
    setMsg({ text, error })
    setTimeout(() => setMsg(null), 4000)
  }

  return (
    <AdminShell active="config">
      <div className="space-y-5">
        <div>
          <h1 className="text-xl font-bold">Configurações</h1>
          <p className="text-sm text-klarim-muted">
            Ajuste os parâmetros operacionais ao vivo (o banco tem prioridade sobre o
            <code className="mx-1 rounded bg-klarim-border/50 px-1">.env</code>) — sem redeploy.
          </p>
        </div>

        {msg && (
          <div className={`rounded-lg px-4 py-2 text-sm ${msg.error ? 'bg-klarim-fail/15 text-klarim-fail' : 'bg-klarim-ok/15 text-klarim-ok'}`}>
            {msg.text}
          </div>
        )}

        {/* --- Parâmetros operacionais --- */}
        <Card title="Parâmetros operacionais">
          {cfg.loading ? <Loading /> : cfg.error ? <ErrorBox message={cfg.error} /> : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs uppercase text-klarim-muted">
                    <th className="py-2 pr-4">Parâmetro</th>
                    <th className="py-2 pr-4">Variável</th>
                    <th className="py-2 pr-4">Valor</th>
                    <th className="py-2">Ações</th>
                  </tr>
                </thead>
                <tbody>
                  {(cfg.data?.params || []).map((p) => (
                    <ParamRow key={p.key} p={p} onChanged={() => cfg.reload()} flash={flash} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* --- Segurança --- */}
        <SecuritySection mcpMasked={cfg.data?.mcp_token_masked} passwordSource={cfg.data?.password_source} flash={flash} />

        {/* --- Informações --- */}
        <Card title="Informações">
          {info.loading ? <Loading /> : info.error ? <ErrorBox message={info.error} /> : (
            <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm sm:grid-cols-4">
              <Info label="Versão" value={info.data?.version} />
              <Info label="Uptime da API" value={fmtUptime(info.data?.uptime_seconds)} />
              <Info label="Redis" value={info.data?.redis_connected ? '🟢 conectado' : '🔴 offline'} />
              <Info label="Último start" value={info.data?.started_at ? formatDate(info.data.started_at) : '—'} />
            </dl>
          )}
        </Card>
      </div>
    </AdminShell>
  )
}

function Info({ label, value }) {
  return (
    <div>
      <dt className="text-xs uppercase text-klarim-muted">{label}</dt>
      <dd className="mt-0.5 font-semibold">{value ?? '—'}</dd>
    </div>
  )
}

function ParamRow({ p, onChanged, flash }) {
  const [editing, setEditing] = useState(false)
  const [val, setVal] = useState(String(p.value))
  const [busy, setBusy] = useState(false)

  async function save() {
    setBusy(true)
    try {
      await admin.configPut(p.key, val)
      setEditing(false); flash(`${p.label} atualizado.`); onChanged()
    } catch (e) { flash(errText(e), true) } finally { setBusy(false) }
  }
  async function reset() {
    setBusy(true)
    try {
      await admin.configReset(p.key)
      flash(`${p.label} voltou ao .env.`); onChanged()
    } catch (e) { flash(errText(e), true) } finally { setBusy(false) }
  }

  return (
    <tr className="border-t border-klarim-border">
      <td className="py-2 pr-4">{p.label}</td>
      <td className="py-2 pr-4"><code className="text-xs text-klarim-muted">{p.key}</code></td>
      <td className="py-2 pr-4">
        {editing ? (
          <span className="inline-flex items-center gap-1">
            <input type="number" min={p.min} max={p.max} value={val} autoFocus
              onChange={(e) => setVal(e.target.value)}
              className="w-24 rounded border border-klarim-border bg-klarim-bg px-2 py-1 text-sm" />
            <button onClick={save} disabled={busy} title="Salvar" className="px-1 text-klarim-ok">✓</button>
            <button onClick={() => { setEditing(false); setVal(String(p.value)) }} title="Cancelar" className="px-1 text-klarim-muted">✗</button>
          </span>
        ) : (
          <span className="inline-flex items-center gap-2">
            <span className="font-semibold">{p.value}</span>
            <span className="text-xs font-normal text-klarim-muted">{p.unit}</span>
            {p.source === 'db' ? <Badge color="#F0C000">db</Badge> : <Badge color="#8B949E">env</Badge>}
          </span>
        )}
        <div className="text-[11px] text-klarim-muted">faixa {p.min}–{p.max}{p.source === 'db' ? ` · .env: ${p.env_value}` : ''}</div>
      </td>
      <td className="py-2">
        {!editing && (
          <span className="inline-flex gap-2">
            <button onClick={() => { setEditing(true); setVal(String(p.value)) }} title="Editar" className="text-klarim-muted hover:text-klarim-text">✏️</button>
            {p.source === 'db' && <Button size="sm" variant="ghost" onClick={reset} disabled={busy}>Resetar</Button>}
          </span>
        )}
      </td>
    </tr>
  )
}

function SecuritySection({ mcpMasked, passwordSource, flash }) {
  const [cur, setCur] = useState(''); const [nw, setNw] = useState(''); const [cf, setCf] = useState('')
  const [pwBusy, setPwBusy] = useState(false)
  const [rotateOpen, setRotateOpen] = useState(false)
  const [rotatePw, setRotatePw] = useState(''); const [rotateBusy, setRotateBusy] = useState(false)
  const [newToken, setNewToken] = useState(null)

  async function changePw(e) {
    e.preventDefault()
    if (nw !== cf) return flash('As senhas não coincidem.', true)
    setPwBusy(true)
    try {
      await admin.changePassword({ current_password: cur, new_password: nw, confirm_password: cf })
      flash('Senha alterada com sucesso.'); setCur(''); setNw(''); setCf('')
    } catch (e2) { flash(errText(e2), true) } finally { setPwBusy(false) }
  }

  async function rotate() {
    setRotateBusy(true)
    try {
      const data = await admin.rotateMcpToken(rotatePw)
      setNewToken(data.new_token); setRotateOpen(false); setRotatePw(''); flash('Token MCP rotacionado.')
    } catch (e) { flash(errText(e), true) } finally { setRotateBusy(false) }
  }

  const field = 'w-full rounded-lg border border-klarim-border bg-klarim-bg px-3 py-2 text-sm'

  return (
    <Card title="Segurança">
      <div className="grid gap-6 md:grid-cols-2">
        {/* Alterar senha */}
        <form onSubmit={changePw} className="space-y-2">
          <h3 className="text-sm font-semibold">Alterar senha do admin</h3>
          <input type="password" placeholder="Senha atual" value={cur} onChange={(e) => setCur(e.target.value)} autoComplete="current-password" className={field} />
          <input type="password" placeholder="Nova senha" value={nw} onChange={(e) => setNw(e.target.value)} autoComplete="new-password" className={field} />
          <input type="password" placeholder="Confirmar nova senha" value={cf} onChange={(e) => setCf(e.target.value)} autoComplete="new-password" className={field} />
          <p className="text-[11px] text-klarim-muted">Mínimo 12 caracteres, com maiúscula, minúscula e número. Origem atual: <strong>{passwordSource || 'env'}</strong>.</p>
          <Button type="submit" variant="primary" disabled={pwBusy || !cur || !nw}>{pwBusy ? 'Salvando…' : 'Alterar senha'}</Button>
        </form>

        {/* Token MCP */}
        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Token MCP (estático)</h3>
          <p className="text-sm text-klarim-muted">Token atual: <code className="text-klarim-text">{mcpMasked || 'n/d'}</code></p>
          {newToken ? (
            <div className="rounded-lg border border-klarim-alert/40 bg-klarim-alert/10 p-3">
              <p className="text-xs text-klarim-muted">Novo token (mostrado uma única vez — salve agora):</p>
              <code className="mt-1 block break-all text-xs text-klarim-text">{newToken}</code>
              <div className="mt-2 flex gap-2">
                <Button size="sm" onClick={() => { navigator.clipboard?.writeText(newToken); flash('Token copiado.') }}>Copiar</Button>
                <Button size="sm" variant="ghost" onClick={() => setNewToken(null)}>Fechar</Button>
              </div>
            </div>
          ) : (
            <Button variant="danger" onClick={() => setRotateOpen(true)}>Rotacionar token</Button>
          )}
          <p className="text-[11px] text-klarim-muted">Ao rotacionar, conexões CLI com o token antigo param. Conexões OAuth (login) não são afetadas.</p>
        </div>
      </div>

      {rotateOpen && (
        <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/60 p-4" onClick={() => setRotateOpen(false)}>
          <div className="w-full max-w-sm rounded-xl border border-klarim-border bg-klarim-surface p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold">Confirmar rotação do token MCP</h3>
            <p className="mt-1 text-xs text-klarim-muted">Digite sua senha para confirmar.</p>
            <input type="password" placeholder="Senha do admin" value={rotatePw} onChange={(e) => setRotatePw(e.target.value)} autoFocus className={`${field} mt-3`} />
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setRotateOpen(false)}>Cancelar</Button>
              <Button variant="danger" onClick={rotate} disabled={rotateBusy || !rotatePw}>{rotateBusy ? 'Rotacionando…' : 'Rotacionar'}</Button>
            </div>
          </div>
        </div>
      )}
    </Card>
  )
}
