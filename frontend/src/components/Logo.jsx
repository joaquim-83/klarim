// Logo Klarim: beacon (farol de alerta) + wordmark "KLARIM" com o R em destaque.
export function Beacon({ size = 32 }) {
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true">
      <g stroke="#FF6B35" strokeWidth="3.5" strokeLinecap="round">
        <line x1="32" y1="4" x2="32" y2="13" />
        <line x1="12" y1="12" x2="18" y2="18" />
        <line x1="52" y1="12" x2="46" y2="18" />
        <line x1="6" y1="30" x2="15" y2="30" />
        <line x1="58" y1="30" x2="49" y2="30" />
      </g>
      <circle cx="32" cy="38" r="17" fill="#FF6B35" />
      <circle cx="32" cy="38" r="7.5" fill="#0D1117" />
    </svg>
  )
}

export default function Logo({ size = 32, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Beacon size={size} />
      <span className="text-2xl font-extrabold tracking-widest">
        KLA<span className="text-klarim-alert">R</span>IM
      </span>
    </span>
  )
}
