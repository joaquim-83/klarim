import { SEMAPHORE_COLORS } from '../lib/constants'

// Círculo grande do semáforo com o score (mesmo visual do PDF).
export default function Semaphore({ score, semaphore }) {
  const color = SEMAPHORE_COLORS[semaphore] || SEMAPHORE_COLORS.vermelho
  const label = (semaphore || '').toUpperCase()
  return (
    <div className="flex flex-col items-center">
      <div
        className="flex h-40 w-40 flex-col items-center justify-center rounded-full sm:h-48 sm:w-48"
        style={{ border: `11px solid ${color}` }}
      >
        <span className="text-5xl font-extrabold leading-none sm:text-6xl" style={{ color }}>
          {score}
        </span>
        <span className="mt-1 text-sm text-klarim-muted">/ 100</span>
      </div>
      <span className="mt-3 text-2xl font-extrabold tracking-wide" style={{ color }}>
        {label}
      </span>
    </div>
  )
}
