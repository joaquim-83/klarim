import { Link } from 'react-router-dom'

export default function Footer() {
  return (
    <footer className="mt-16 border-t border-klarim-border">
      <div className="mx-auto max-w-3xl px-4 py-6 text-center text-sm text-klarim-muted">
        <p>Klarim Scanner — Varredura 100% passiva. Nenhum dado é acessado.</p>
        <nav className="mt-2 flex flex-wrap justify-center gap-4">
          <a href="#" className="text-klarim-muted hover:text-klarim-text">Sobre</a>
          <a href="#" className="text-klarim-muted hover:text-klarim-text">Parceiros</a>
          <a href="mailto:scan@klarim.net" className="text-klarim-muted hover:text-klarim-text">Contato</a>
          <Link to="/recuperar" className="text-klarim-muted hover:text-klarim-text">Recuperar relatórios</Link>
        </nav>
      </div>
    </footer>
  )
}
