import { Routes, Route, Navigate } from 'react-router-dom'
import Landing from './pages/Landing'
import Scan from './pages/Scan'
import Result from './pages/Result'
import Payment from './pages/Payment'
import Report from './pages/Report'
import Recuperar from './pages/Recuperar'
import RecuperarAcesso from './pages/RecuperarAcesso'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/scan" element={<Scan />} />
      <Route path="/result" element={<Result />} />
      <Route path="/pay" element={<Payment />} />
      <Route path="/report" element={<Report />} />
      <Route path="/recuperar" element={<Recuperar />} />
      <Route path="/recuperar/acesso" element={<RecuperarAcesso />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
