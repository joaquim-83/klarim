import { useEffect, useState } from 'react'
import { gatePlanCtaHref, gatePlanCtaLabel } from '../../lib/nav.js'

// KL-150 (Fix 2) — CTA de plano da landing /security-gate ciente do estado de auth.
// No mount consulta GET /api/account/gate/status (cookie de sessão HttpOnly, same-origin):
//   logado   → Free abre o portal · Pro/Team vão ao portal já disparando o upgrade (?upgrade=)
//   deslogado → passa pelo cadastro developer (que, após o signup, redireciona ao portal c/ upgrade)
// SSR-safe: o 1º paint é o estado DESLOGADO (link funcional sem JS); se o usuário logado clicar antes
// do fetch resolver, o /cadastrar detecta a sessão no servidor e redireciona ao portal (KL-157).
// `slug` = free|pro|team|enterprise; `className` = estilo do botão (vem do .astro, fonte única).
export default function GatePlanCTA({ slug, className = '' }) {
  const [loggedIn, setLoggedIn] = useState(false)
  useEffect(() => {
    let alive = true
    fetch('/api/account/gate/status', { credentials: 'include' })
      .then((r) => { if (alive) setLoggedIn(r.ok) })
      .catch(() => { /* deslogado / backend fora → mantém o link de cadastro */ })
    return () => { alive = false }
  }, [])
  return (
    <a href={gatePlanCtaHref(slug, loggedIn)} className={className} data-plan-cta={slug}>
      {gatePlanCtaLabel(slug)}
    </a>
  )
}
