import { useState } from 'react'
import ContactModal from './ContactModal'

// E-mail clicável que abre o modal de contato (não usa mailto). Reutilizado no
// corpo das páginas Sobre e Parceiros.
export default function ContactEmail({ className = '' }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className={`font-medium text-klarim-alert hover:underline ${className}`}
      >
        📧 scan@klarim.net
      </button>
      {open && <ContactModal onClose={() => setOpen(false)} />}
    </>
  )
}
