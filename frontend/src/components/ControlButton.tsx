import type { ReactNode } from 'react'

type Tone = 'accent' | 'error' | 'success'

// Dark-bordered, lightly color-filled buttons. The full class strings are
// literal so Tailwind's scanner keeps them.
const TONE: Record<Tone, string> = {
  accent:
    'border-accent/50 bg-accent/10 text-accent hover:bg-accent/20 hover:border-accent/70',
  error:
    'border-error/50 bg-error/10 text-error hover:bg-error/20 hover:border-error/70',
  success:
    'border-success/50 bg-success/10 text-success hover:bg-success/20 hover:border-success/70',
}

export function ControlButton({
  tone,
  icon,
  label,
  onClick,
  disabled,
}: {
  tone: Tone
  icon: ReactNode
  label: string
  onClick?: () => void
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center gap-1.5 rounded-field border px-2.5 py-1 font-mono text-xs transition-colors duration-150 disabled:opacity-40 ${TONE[tone]}`}
    >
      <span aria-hidden className="flex-none">
        {icon}
      </span>
      {label}
    </button>
  )
}

const SIZE = { width: 12, height: 12 }

export const RestartIcon = (
  <svg viewBox="0 0 24 24" {...SIZE} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
    <path d="M21 12a9 9 0 1 1-2.6-6.4" />
    <path d="M21 3v5h-5" />
  </svg>
)

export const StopIcon = (
  <svg viewBox="0 0 24 24" {...SIZE}>
    <rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor" />
  </svg>
)

export const StartIcon = (
  <svg viewBox="0 0 24 24" {...SIZE}>
    <path d="M7 5.5v13l11-6.5z" fill="currentColor" />
  </svg>
)
