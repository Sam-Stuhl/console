import type { Project } from '../api/client'

interface Status {
  label: string
  dot: string
  tone: string
}

// One indicator that answers "what is this app doing right now": an in-flight
// deploy wins (it is transient and actionable), then the live/down health, then
// a failed deploy, then never-deployed. Mirrors the website tile plus the
// in-progress deploy states.
export function projectStatus(p: Project): Status {
  const d = p.deploy_status
  if (d === 'queued' || d === 'building' || d === 'deploying') {
    const dot =
      d === 'building' ? 'bg-info' : d === 'deploying' ? 'bg-warning' : 'bg-base-300'
    return { label: d, dot: `${dot} motion-safe:animate-pulse`, tone: 'text-muted' }
  }
  if (p.health === 'down') return { label: 'down', dot: 'bg-error', tone: 'text-error' }
  if (p.health === 'up' || p.is_live) return { label: 'live', dot: 'bg-success', tone: '' }
  if (d === 'failed') return { label: 'failed', dot: 'bg-error', tone: 'text-error' }
  return { label: 'not deployed', dot: 'bg-base-300', tone: 'text-muted' }
}

export default function ProjectStatusBadge({ project }: { project: Project }) {
  const { label, dot, tone } = projectStatus(project)
  return (
    <span className="inline-flex items-center gap-2 whitespace-nowrap font-mono text-xs">
      <span aria-hidden className={`size-1.5 rounded-full ${dot}`} />
      <span className={tone}>{label}</span>
    </span>
  )
}
