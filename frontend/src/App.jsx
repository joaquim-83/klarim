import { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { trackEvent, initTracking } from './lib/tracker'
import Landing from './pages/Landing'
import Scan from './pages/Scan'
import Result from './pages/Result'
import Payment from './pages/Payment'
import Report from './pages/Report'
import Recuperar from './pages/Recuperar'
import RecuperarAcesso from './pages/RecuperarAcesso'
import Sobre from './pages/Sobre'
import Parceiros from './pages/Parceiros'
import Monitorados from './pages/Monitorados'
import MonitorarAprovar from './pages/MonitorarAprovar'

// KL-51 fase 2: o painel admin (/painel/*) foi 100% migrado para o Astro (web/). O Nginx
// roteia TODO /painel → container `astro`, então as rotas do painel foram removidas daqui
// (evita duplicação e não baixa o bundle do painel/Recharts no build público). O código
// antigo continua em frontend/src/pages/admin/ e components/admin/ como referência, fora do
// build (sem import). O Vite ainda serve o fluxo de scan legado (/result, /pay, /report) e
// as páginas ainda não migradas.

// KL-21: page_view a cada rota do site público (não trackeia o painel admin).
function RouteTracker() {
  const location = useLocation()
  useEffect(() => {
    initTracking()
  }, [])
  useEffect(() => {
    if (location.pathname.startsWith('/painel')) return
    trackEvent('page_view', { page: location.pathname })
  }, [location.pathname, location.search])
  return null
}

export default function App() {
  return (
    <>
    <RouteTracker />
    <Routes>
      {/* Site público */}
      <Route path="/" element={<Landing />} />
      <Route path="/scan" element={<Scan />} />
      <Route path="/result" element={<Result />} />
      <Route path="/pay" element={<Payment />} />
      <Route path="/report" element={<Report />} />
      <Route path="/recuperar" element={<Recuperar />} />
      <Route path="/recuperar/acesso" element={<RecuperarAcesso />} />
      <Route path="/sobre" element={<Sobre />} />
      <Route path="/parceiros" element={<Parceiros />} />
      <Route path="/monitorados" element={<Monitorados />} />
      <Route path="/monitorados/aprovar" element={<MonitorarAprovar />} />

      {/* Painel admin (/painel/*) migrado para o Astro (KL-51 fase 2) — servido pelo Nginx → astro. */}

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </>
  )
}
