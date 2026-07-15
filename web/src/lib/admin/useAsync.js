import { useState, useEffect, useCallback } from 'react'

// Hook simples de carregamento assíncrono com estado de erro e reload.
// Portado de frontend/src/lib/useAsync.js (KL-51 fase 1) — sem alteração.
export function useAsync(fn, deps = []) {
  const [data, setData] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const run = useCallback(() => {
    setLoading(true)
    setError('')
    return Promise.resolve(fn())
      .then((d) => setData(d))
      .catch((e) => setError(e?.message || String(e)))
      .finally(() => setLoading(false))
  }, deps)

  useEffect(() => {
    run()
  }, [run])

  return { data, error, loading, reload: run, setData }
}

// Atrasa a propagação de `value` em `delay` ms — usado na busca (não dispara
// request a cada tecla, só quando o usuário para de digitar).
export function useDebounce(value, delay = 300) {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay)
    return () => clearTimeout(timer)
  }, [value, delay])
  return debounced
}
