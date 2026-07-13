import { lazy, Suspense, useEffect } from 'react'
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
import ProtectedRoute from './components/admin/ProtectedRoute'

// Dashboard admin (KL-14) — carregado sob demanda (code-split), para o site
// público não baixar o bundle do painel (Recharts etc.).
const AdminLayout = lazy(() => import('./components/admin/AdminLayout'))
const Login = lazy(() => import('./pages/admin/Login'))
const Overview = lazy(() => import('./pages/admin/Overview'))
const Alvos = lazy(() => import('./pages/admin/Alvos'))
const AlvoDetalhe = lazy(() => import('./pages/admin/AlvoDetalhe'))
const Scans = lazy(() => import('./pages/admin/Scans'))
const ScanDetalhe = lazy(() => import('./pages/admin/ScanDetalhe'))
const Alertas = lazy(() => import('./pages/admin/Alertas'))
const Pagamentos = lazy(() => import('./pages/admin/Pagamentos'))
const Rescans = lazy(() => import('./pages/admin/Rescans'))
const Analytics = lazy(() => import('./pages/admin/Analytics'))
const Clientes = lazy(() => import('./pages/admin/Clientes'))
const Sistema = lazy(() => import('./pages/admin/Sistema'))
const Config = lazy(() => import('./pages/admin/Config'))

function AdminFallback() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-klarim-bg text-klarim-muted">
      Carregando painel…
    </div>
  )
}

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

      {/* Dashboard admin */}
      <Route
        path="/painel/login"
        element={<Suspense fallback={<AdminFallback />}><Login /></Suspense>}
      />
      <Route
        path="/painel"
        element={
          <ProtectedRoute>
            <Suspense fallback={<AdminFallback />}>
              <AdminLayout />
            </Suspense>
          </ProtectedRoute>
        }
      >
        <Route index element={<Overview />} />
        <Route path="alvos" element={<Alvos />} />
        <Route path="alvos/:id" element={<AlvoDetalhe />} />
        <Route path="scans" element={<Scans />} />
        <Route path="scans/:id" element={<ScanDetalhe />} />
        <Route path="alertas" element={<Alertas />} />
        <Route path="pagamentos" element={<Pagamentos />} />
        <Route path="rescans" element={<Rescans />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="clientes" element={<Clientes />} />
        <Route path="monitorados" element={<Navigate to="/painel/clientes" replace />} />
        <Route path="sistema" element={<Sistema />} />
        <Route path="config" element={<Config />} />
      </Route>

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
    </>
  )
}
