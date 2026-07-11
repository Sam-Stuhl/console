const DOT: Record<string, string> = {
  running: 'bg-warning motion-safe:animate-pulse',
  succeeded: 'bg-success',
  failed: 'bg-error',
}

export default function CommandBadge({
  status,
  exitCode,
}: {
  status: string
  exitCode: number | null
}) {
  const label =
    status === 'failed' && exitCode !== null ? `failed (exit ${exitCode})` : status
  const tone = status === 'failed' ? 'text-error' : status === 'running' ? 'text-muted' : ''
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-mono text-xs">
      <span aria-hidden className={`size-1.5 rounded-full ${DOT[status] ?? 'bg-base-300'}`} />
      <span className={tone}>{label}</span>
    </span>
  )
}
