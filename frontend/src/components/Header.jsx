import { Link } from 'react-router-dom'
import Logo from './Logo'

export default function Header() {
  return (
    <header className="border-b border-klarim-border">
      <div className="mx-auto flex max-w-3xl items-center px-4 py-4">
        <Link to="/" className="text-klarim-text no-underline">
          <Logo />
        </Link>
      </div>
    </header>
  )
}
