import Layout from '../components/Layout'
import ContactEmail from '../components/ContactEmail'

const CATEGORIES = [
  {
    title: 'Desenvolvimento e segurança web',
    desc: 'agências, freelancers e empresas que corrigem vulnerabilidades, implementam boas práticas e fortalecem a segurança dos sites dos clientes.',
  },
  {
    title: 'Consultoria LGPD e compliance',
    desc: 'profissionais e escritórios que auxiliam empresas na adequação à Lei Geral de Proteção de Dados.',
  },
  {
    title: 'Infraestrutura e DevOps',
    desc: 'especialistas em servidores, cloud, certificados SSL e configuração de ambientes seguros.',
  },
  {
    title: 'Pentest e segurança ofensiva',
    desc: 'empresas que realizam testes de intrusão e análises aprofundadas de vulnerabilidades.',
  },
]

export default function Parceiros() {
  return (
    <Layout>
      <article className="mx-auto max-w-2xl">
        <h1 className="text-3xl font-bold">Programa de Parceiros</h1>

        <div className="mt-6 space-y-4 leading-relaxed text-klarim-text">
          <p>
            O Klarim identifica problemas de segurança em sites e sistemas web todos
            os dias. Muitos dos nossos clientes precisam de ajuda profissional para
            corrigir as vulnerabilidades encontradas — e é aí que você entra.
          </p>

          <p><strong>Buscamos parceiros em:</strong></p>

          <ul className="space-y-3">
            {CATEGORIES.map((c) => (
              <li key={c.title}>
                <strong>{c.title}</strong> — {c.desc}
              </li>
            ))}
          </ul>

          <p>
            <strong>Como funciona:</strong> quando um cliente do Klarim precisa de
            ajuda para corrigir as falhas identificadas no relatório, indicamos nossos
            parceiros. Você recebe leads qualificados — empresas que já sabem que têm
            um problema e querem resolver.
          </p>
          <p>
            <strong>Quer ser parceiro?</strong> Entre em contato informando sua área
            de atuação, região e um breve resumo dos serviços que oferece.
          </p>
        </div>

        <div className="mt-8">
          <ContactEmail />
        </div>
      </article>
    </Layout>
  )
}
