import Header from './Header'
import Footer from './Footer'

// Envolve as páginas com header, conteúdo centralizado e footer.
export default function Layout({ children, withHeader = true, withFooter = true }) {
  return (
    <div className="flex min-h-screen flex-col bg-klarim-bg text-klarim-text">
      {withHeader && <Header />}
      <main className="mx-auto w-full max-w-3xl flex-1 px-4 py-8">{children}</main>
      {withFooter && <Footer />}
    </div>
  )
}
