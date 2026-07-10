import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { fetchSummary } from './api'

// Obtém o resumo do scan: usa o que veio via navegação (state) ou, se a página
// foi aberta diretamente por link, busca o resultado já existente. Sem verificação
// de e-mail e sem resultado em cache, redireciona à home para verificar (KL-25).
export function useSummary(url) {
  const location = useLocation()
  const navigate = useNavigate()
  const initial = location.state?.summary || null
  const [summary, setSummary] = useState(initial)
  const [loading, setLoading] = useState(!initial)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initial || !url) return
    setLoading(true)
    fetchSummary(url)
      .then(setSummary)
      .catch((e) => {
        if (e.message === 'auth_required') { navigate(`/?url=${encodeURIComponent(url)}`, { replace: true }); return }
        setError(e.message || 'Erro ao escanear.')
      })
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  return { summary, loading, error }
}

export function problemLine(n, total = 15) {
  const base = `Analisamos ${total} pontos de segurança do seu site`
  if (!n) return `${base} e nenhuma vulnerabilidade foi encontrada.`
  if (n === 1) return `${base} e encontramos 1 vulnerabilidade.`
  return `${base} e encontramos ${n} vulnerabilidades.`
}
