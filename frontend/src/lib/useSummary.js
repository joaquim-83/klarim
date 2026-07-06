import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { fetchSummary } from './api'

// Obtém o resumo do scan: usa o que veio via navegação (state) ou, se a página
// foi aberta diretamente por link, refaz a varredura.
export function useSummary(url) {
  const location = useLocation()
  const initial = location.state?.summary || null
  const [summary, setSummary] = useState(initial)
  const [loading, setLoading] = useState(!initial)
  const [error, setError] = useState('')

  useEffect(() => {
    if (initial || !url) return
    setLoading(true)
    fetchSummary(url)
      .then(setSummary)
      .catch((e) => setError(e.message || 'Erro ao escanear.'))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url])

  return { summary, loading, error }
}

export function problemLine(n) {
  if (!n) return 'Nenhum problema de segurança foi encontrado no seu site.'
  if (n === 1) return 'Encontramos 1 problema de segurança no seu site.'
  return `Encontramos ${n} problemas de segurança no seu site.`
}
