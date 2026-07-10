const DOT: Record<string, string> = {
  building: 'bg-info motion-safe:animate-pulse',
  queued: 'bg-base-300 motion-safe:animate-pulse',
  deploying: 'bg-warning motion-safe:animate-pulse',
  live: 'bg-success',
  superseded: 'bg-base-300',
  failed: 'bg-error',
}

export default function DeployBadge({
  status,
  substate,
}: {
  status: string
  substate: string | null
}) {
  const label = status === 'deploying' && substate ? `deploying (${substate})` : status
  const tone =
    status === 'failed' ? 'text-error' : status === 'live' ? '' : 'text-muted'
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-mono text-xs">
      <span aria-hidden className={`size-1.5 rounded-full ${DOT[status] ?? 'bg-base-300'}`} />
      <span className={tone}>{label}</span>
    </span>
  )
}
