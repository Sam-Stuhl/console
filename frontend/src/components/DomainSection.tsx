import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { changeProjectDomain, fetchDomains, type Repoint } from '../api/client'

/**
 * Move a project to a different base domain. Only rendered when more than one
 * domain is configured (otherwise there is nowhere to move it). The change only
 * reaches Traefik on the next deploy, so we always surface that reminder.
 */
export default function DomainSection({
  projectId,
  subdomain,
  currentDomain,
  isProtected,
}: {
  projectId: string
  subdomain: string
  currentDomain: string
  isProtected: boolean
}) {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['domains'], queryFn: fetchDomains })
  const domains = data?.domains ?? []
  const others = domains.filter((d) => d !== currentDomain)

  const [editing, setEditing] = useState(false)
  const [target, setTarget] = useState('')
  const [repoint, setRepoint] = useState<Repoint>('auto')
  const [note, setNote] = useState<string | null | undefined>(undefined)

  const chosen = target || others[0] || ''

  const change = useMutation({
    mutationFn: () => changeProjectDomain(projectId, chosen, repoint),
    onSuccess: (res) => {
      setNote(res.note)
      setEditing(false)
      setTarget('')
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  if (domains.length <= 1) return null // nothing to move to

  return (
    <div className="flex max-w-2xl flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
        <span className="text-muted">domain</span>
        <span className="text-base-content">{currentDomain}</span>
        {!editing && (
          <button
            type="button"
            onClick={() => {
              setEditing(true)
              setNote(undefined)
            }}
            className="text-accent transition-colors duration-150 hover:underline"
          >
            change
          </button>
        )}
      </div>

      {editing && (
        <div className="flex flex-col gap-2 rounded-box border border-base-300 bg-base-100 px-3.5 py-3">
          <label className="flex flex-col gap-1">
            <span className="font-mono text-xs text-muted">move to</span>
            <select
              value={chosen}
              onChange={(e) => setTarget(e.target.value)}
              className="select select-sm w-full max-w-xs border-base-300 bg-base-200 font-mono text-sm"
            >
              {others.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <span className="font-mono text-xs text-faint">
              serves at {subdomain}.{chosen} after the next deploy
            </span>
          </label>

          {isProtected && (
            <fieldset className="flex flex-col gap-1.5 pt-1">
              <legend className="font-mono text-[11px] uppercase tracking-wide text-muted">
                cloudflare access is on for this app
              </legend>
              <Radio
                checked={repoint === 'auto'}
                onChange={() => setRepoint('auto')}
                label="move the access gate for me"
                hint="recreates the login gate on the new hostname"
              />
              <Radio
                checked={repoint === 'manual'}
                onChange={() => setRepoint('manual')}
                label="I'll move it in cloudflare myself"
                hint="the console leaves the gate untouched"
              />
            </fieldset>
          )}

          <div className="flex items-center gap-3 pt-1">
            <button
              type="button"
              disabled={!chosen || change.isPending}
              onClick={() => change.mutate()}
              className="btn btn-primary btn-sm font-mono"
            >
              {change.isPending ? 'changing…' : 'change domain'}
            </button>
            <button
              type="button"
              onClick={() => setEditing(false)}
              className="font-mono text-xs text-muted transition-colors duration-150 hover:text-base-content"
            >
              cancel
            </button>
          </div>
          {change.isError && (
            <p className="font-mono text-xs text-error">{(change.error as Error).message}</p>
          )}
        </div>
      )}

      {note !== undefined && (
        <div className="rounded-box border border-warning/40 bg-warning/5 px-3 py-2 font-mono text-xs leading-relaxed text-warning">
          Domain changed. Redeploy the app (push the repo, or use{' '}
          <span className="text-base-content">redeploy</span> on the latest deployment) to
          route the new hostname.
          {note ? ` ${note}` : ''}
        </div>
      )}
    </div>
  )
}

function Radio({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean
  onChange: () => void
  label: string
  hint: string
}) {
  return (
    <label className="flex items-start gap-2 font-mono text-xs">
      <input
        type="radio"
        checked={checked}
        onChange={onChange}
        className="radio radio-xs mt-0.5"
      />
      <span className="flex flex-col">
        <span className="text-base-content">{label}</span>
        <span className="text-faint">{hint}</span>
      </span>
    </label>
  )
}
