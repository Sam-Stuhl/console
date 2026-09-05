import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addAccessPath,
  fetchAccessPaths,
  removeAccessPath,
  type AccessPath,
} from '../api/client'

// Paths on the console's own hostname that something depends on. Closing one
// does not just gate a path: whatever calls it gets a login page instead.
const LOAD_BEARING: Record<string, string> = {
  v1: 'Scripts and agents call the API here. Closing it cuts them off until it is opened again.',
  mcp: 'AI agents connect here. Closing it cuts them off until it is opened again.',
}

/**
 * Cloudflare Access bypass paths for one hostname: an app's (projectId set) or
 * the console's own (projectId null).
 *
 * Adding is the dangerous direction here, not removing, so the two-step
 * confirm sits on the add. Closing a path takes one click: it restores the
 * login, and re-adding it is a retype.
 */
export default function AccessPathsSection({ projectId }: { projectId: string | null }) {
  const queryClient = useQueryClient()
  const key = ['access-paths', projectId ?? 'console']
  const { data, isLoading } = useQuery({
    queryKey: key,
    queryFn: () => fetchAccessPaths(projectId),
  })

  const [newPath, setNewPath] = useState('')
  const [confirming, setConfirming] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  // The row waiting on a second click, for a path something else depends on.
  const [closing, setClosing] = useState<string | null>(null)

  const done = () => {
    setActionError(null)
    setConfirming(null)
    setClosing(null)
    setNewPath('')
    queryClient.invalidateQueries({ queryKey: key })
  }

  const open = useMutation({
    mutationFn: (path: string) => addAccessPath(projectId, path),
    onSuccess: done,
    onError: (err: Error) => setActionError(err.message),
  })

  const close = useMutation({
    mutationFn: (pathId: string) => removeAccessPath(projectId, pathId),
    onSuccess: done,
    onError: (err: Error) => setActionError(err.message),
  })

  if (isLoading || !data) return <span className="skeleton h-8 w-full max-w-md" />

  const paths = data.paths
  const pending = confirming?.trim().replace(/^\/+|\/+$/g, '') ?? ''

  return (
    <div className="flex max-w-2xl flex-col gap-3">
      <p className="font-mono text-xs leading-relaxed text-faint">
        Paths that skip the Cloudflare login, so a script, a Shortcut, or a
        webhook can call them. Everything else on the site keeps its gate.
      </p>

      {paths.length > 0 && (
        <ul className="flex flex-col font-mono text-xs">
          {paths.map((p: AccessPath) => (
            <li
              key={p.id}
              className="flex flex-wrap items-center justify-between gap-3 border-b border-base-300/40 py-1.5 last:border-none"
            >
              <span className="flex min-w-0 flex-wrap items-baseline gap-2">
                <span className="truncate">
                  <span className="text-muted">{p.hostname}</span>/{p.path}
                </span>
                {p.hostname !== data.hostname && (
                  <span className="text-warning">old hostname</span>
                )}
              </span>
              {closing === p.id ? (
                // basis-full so the warning gets its own line under the path
                // rather than being pushed off the right edge of the section.
                <span className="flex basis-full flex-wrap items-center gap-3">
                  <span className="text-warning">{LOAD_BEARING[p.path]}</span>
                  <button
                    type="button"
                    disabled={close.isPending}
                    className="text-error/80 transition-colors duration-150 hover:text-error hover:underline"
                    onClick={() => close.mutate(p.id)}
                  >
                    close anyway
                  </button>
                  <button
                    type="button"
                    className="text-muted transition-colors duration-150 hover:text-base-content"
                    onClick={() => setClosing(null)}
                  >
                    keep
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  disabled={close.isPending}
                  className="shrink-0 text-error/80 transition-colors duration-150 hover:text-error hover:underline"
                  onClick={() =>
                    projectId === null && LOAD_BEARING[p.path]
                      ? setClosing(p.id)
                      : close.mutate(p.id)
                  }
                >
                  close
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {paths.length === 0 && (
        <p className="font-mono text-xs text-muted">no open paths</p>
      )}

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          setActionError(null)
          if (newPath.trim()) setConfirming(newPath)
        }}
      >
        <label className="flex h-8 items-center rounded-field border border-base-300 bg-base-100 pl-2 font-mono text-xs focus-within:border-base-content/30">
          <span className="whitespace-nowrap text-muted">{data.hostname}</span>
          <input
            value={newPath}
            onChange={(e) => setNewPath(e.target.value)}
            placeholder={projectId ? '/api' : '/v1'}
            className="w-44 bg-transparent pr-2 font-mono text-xs outline-none placeholder:text-faint"
          />
        </label>
        <button type="submit" className="btn btn-ghost btn-sm font-mono text-muted">
          open path
        </button>
      </form>

      {confirming && (
        <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
          <span className="text-warning">
            anyone on the internet can reach {data.hostname}/{pending}. open it?
          </span>
          <button
            type="button"
            disabled={open.isPending}
            className="text-error/80 transition-colors duration-150 hover:text-error hover:underline"
            onClick={() => open.mutate(confirming)}
          >
            confirm
          </button>
          <button
            type="button"
            className="text-muted transition-colors duration-150 hover:text-base-content"
            onClick={() => setConfirming(null)}
          >
            cancel
          </button>
        </div>
      )}

      <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
        {projectId
          ? 'Whatever the app checks itself is then the only thing in front of that path, so open one only where the app authenticates its own callers.'
          : 'The machine paths of this console: v1 and mcp, for scripts and agents. Each authenticates its own callers with a token, which is why it can skip the login. /api cannot be opened, since it has no authentication of its own.'}{' '}
        Cloudflare rate limiting is a separate permission this console does not
        hold, so add a rate limit for an open path in the Cloudflare dashboard.
      </p>

      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}
