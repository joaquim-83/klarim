import { useState } from 'react'
import { Link } from 'react-router-dom'
import ContactModal from './ContactModal'

export default function Footer() {
  const [showContact, setShowContact] = useState(false)

  return (
    <footer className="mt-16 border-t border-klarim-border">
      <div className="mx-auto max-w-3xl px-4 py-6 text-center text-sm text-klarim-muted">
        <p>Klarim Scanner — Varredura 100% passiva. Nenhum dado é acessado.</p>
        <nav className="mt-2 flex flex-wrap justify-center gap-4">
          <Link to="/sobre" className="text-klarim-muted hover:text-klarim-text">Sobre</Link>
          <Link to="/parceiros" className="text-klarim-muted hover:text-klarim-text">Parceiros</Link>
          <Link to="/monitorados" className="text-klarim-muted hover:text-klarim-text">Monitorados</Link>
          <button
            onClick={() => setShowContact(true)}
            className="text-klarim-muted hover:text-klarim-text"
          >
            Contato
          </button>
          <Link to="/recuperar" className="text-klarim-muted hover:text-klarim-text">Recuperar relatórios</Link>
        </nav>
      </div>
      {showContact && <ContactModal onClose={() => setShowContact(false)} />}
    </footer>
  )
}
