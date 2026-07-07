import { useState, useEffect, useCallback } from 'react'

// Hook simples de carregamento assíncrono com estado de erro e reload.
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
