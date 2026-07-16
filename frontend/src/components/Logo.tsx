/**
 * The console's brand mark: a terminal prompt. Strokes use currentColor, so
 * color it with a text-* class (text-primary for the ember brand). Sized by
 * the className (e.g. size-5).
 */
export function ConsoleMark({ className = '' }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 48 48"
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth={2.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <rect x="9" y="12" width="30" height="24" rx="3.5" />
      <path d="M15 20.5l4.5 3.5-4.5 3.5" />
      <line x1="24" y1="30" x2="31" y2="30" />
    </svg>
  )
}
