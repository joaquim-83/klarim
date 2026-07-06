import { Routes, Route, Navigate } from 'react-router-dom'
import Landing from './pages/Landing'
import Scan from './pages/Scan'
import Result from './pages/Result'
import Report from './pages/Report'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/scan" element={<Scan />} />
      <Route path="/result" element={<Result />} />
      <Route path="/report" element={<Report />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
