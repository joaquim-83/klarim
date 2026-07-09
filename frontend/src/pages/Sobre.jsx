import Layout from '../components/Layout'
import ContactEmail from '../components/ContactEmail'

export default function Sobre() {
  return (
    <Layout>
      <article className="mx-auto max-w-2xl">
        <h1 className="text-3xl font-bold">Sobre o Klarim</h1>

        <div className="mt-6 space-y-4 leading-relaxed text-klarim-text">
          <p>
            O Klarim é um scanner de segurança web que funciona como um alarme
            silencioso para o seu site.
          </p>
          <p>
            Nascemos de uma constatação simples: a maioria dos sites brasileiros tem
            falhas de segurança que os donos desconhecem. Não por negligência — por
            falta de visibilidade. Quem administra um hotel, uma clínica ou um
            e-commerce não tem como saber que seu site carrega scripts sem
            verificação, que a conexão dos clientes pode ser interceptada, ou que
            arquivos internos estão acessíveis publicamente. Até que algo aconteça.
          </p>
          <p>O Klarim existe para que nada aconteça.</p>
          <p>
            <strong>Como funciona:</strong> realizamos varreduras passivas e
            automatizadas em sites e sistemas web, identificando vulnerabilidades
            reais sem acessar, invadir ou coletar qualquer dado. Nosso scanner
            verifica criptografia, headers de segurança, exposição de arquivos,
            integridade de scripts de terceiros e outros pontos críticos que afetam a
            proteção dos seus clientes e do seu negócio.
          </p>
          <p>
            <strong>O que entregamos:</strong> um relatório claro com o diagnóstico
            do seu site — o que está seguro, o que precisa de atenção, e como
            corrigir. Sem jargão técnico desnecessário. Sem alarme falso. Com
            recomendações práticas que seu desenvolvedor ou agência pode implementar
            imediatamente.
          </p>
          <p>
            <strong>Para quem é:</strong> empresas e profissionais que têm presença
            digital e querem saber se estão protegidos. Hotéis, clínicas, escolas,
            escritórios, lojas virtuais, portais de condomínio — qualquer negócio que
            colete dados de clientes e precise cumprir a LGPD.
          </p>
        </div>

        <p className="mt-8 text-xl font-semibold italic text-klarim-alert">
          Segurança acessível para quem não tem equipe de segurança. Esse é o Klarim.
        </p>

        <div className="mt-8">
          <ContactEmail />
        </div>
      </article>
    </Layout>
  )
}
