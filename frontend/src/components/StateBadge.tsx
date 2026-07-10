export default function StateBadge({
  state,
  exitCode,
}: {
  state: string
  exitCode: number | null
}) {
  const cls =
    state === 'running'
      ? 'badge-success'
      : state === 'restarting'
        ? 'badge-warning'
        : state === 'paused'
          ? 'badge-info'
          : state === 'exited' && exitCode !== 0
            ? 'badge-error'
            : 'badge-ghost'
  const label = state === 'exited' ? `exited (${exitCode})` : state
  return <span className={`badge badge-soft ${cls}`}>{label}</span>
}
