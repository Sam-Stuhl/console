const DOT: Record<string, string> = {
  running: 'bg-success',
  restarting: 'bg-warning motion-safe:animate-pulse',
  paused: 'bg-info',
  created: 'bg-base-300',
  dead: 'bg-error',
}

export default function StateBadge({
  state,
  exitCode,
}: {
  state: string
  exitCode: number | null
}) {
  const failed = state === 'exited' && exitCode !== 0
  const dot =
    state === 'exited' ? (failed ? 'bg-error' : 'bg-base-300') : (DOT[state] ?? 'bg-base-300')
  const label = state === 'exited' ? `exited (${exitCode})` : state
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-mono text-xs">
      <span aria-hidden className={`size-1.5 rounded-full ${dot}`} />
      <span className={failed ? 'text-error' : state === 'running' ? '' : 'text-muted'}>
        {label}
      </span>
    </span>
  )
}
