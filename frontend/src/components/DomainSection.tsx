import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { changeProjectHostname, fetchDomains, type Repoint } from '../api/client'

/**
 * Where the app serves: {subdomain}.{domain}, both editable here. The change
 * only reaches Traefik on the next deploy, so we always surface that reminder,
 * and a subdomain change needs the repo's console.toml edited too (the server
 * says so in its note).
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
  const domains = data?.domains ?? [currentDomain]

  const [editing, setEditing] = useState(false)
  const [sub, setSub] = useState(subdomain)
  const [domain, setDomain] = useState(currentDomain)
  const [repoint, setRepoint] = useState<Repoint>('auto')
  const [note, setNote] = useState<string | null | undefined>(undefined)

  const trimmed = sub.trim()
  const unchanged = trimmed === subdomain && domain === currentDomain

  const open = () => {
    setSub(subdomain)
    setDomain(currentDomain)
    setNote(undefined)
    setEditing(true)
  }

  const change = useMutation({
    mutationFn: () =>
      changeProjectHostname(projectId, { domain, subdomain: trimmed, repoint }),
    onSuccess: (res) => {
      setNote(res.note)
      setEditing(false)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
  })

  return (
    <div className="flex max-w-2xl flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 font-mono text-xs">
        <span className="text-muted">serves at</span>
        <span className="text-base-content">
          {subdomain}.{currentDomain}
        </span>
        {!editing && (
          <button
            type="button"
            onClick={open}
            className="text-accent transition-colors duration-150 hover:underline"
          >
            change
          </button>
        )}
      </div>

      {editing && (
        <div className="flex flex-col gap-2 rounded-box border border-base-300 bg-base-100 px-3.5 py-3">
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">
              <span className="font-mono text-xs text-muted">subdomain</span>
              <input
                value={sub}
                onChange={(e) => setSub(e.target.value)}
                spellCheck={false}
                autoCapitalize="none"
                className="input input-sm w-48 border-base-300 bg-base-200 font-mono text-sm"
              />
            </label>
            <span className="pb-2 font-mono text-sm text-muted">.</span>
            <label className="flex flex-col gap-1">
              <span className="font-mono text-xs text-muted">domain</span>
              {domains.length > 1 ? (
                <select
                  value={domain}
                  onChange={(e) => setDomain(e.target.value)}
                  className="select select-sm w-full max-w-xs border-base-300 bg-base-200 font-mono text-sm"
                >
                  {domains.map((d) => (
                    <option key={d} value={d}>
                      {d}
                    </option>
                  ))}
                </select>
              ) : (
                <span className="py-1.5 font-mono text-sm text-muted">{currentDomain}</span>
              )}
            </label>
          </div>
          <span className="font-mono text-xs text-faint">
            {unchanged
              ? `serves at ${subdomain}.${currentDomain}`
              : `serves at ${trimmed || '…'}.${domain} after the next deploy`}
          </span>
          {trimmed !== subdomain && (
            <span className="font-mono text-xs text-faint">
              change app.subdomain in the repo&apos;s console.toml to match, or the
              next deploy routes the old hostname back.
            </span>
          )}

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
              disabled={!trimmed || unchanged || change.isPending}
              onClick={() => change.mutate()}
              className="btn btn-primary btn-sm font-mono"
            >
              {change.isPending ? 'changing…' : 'change hostname'}
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
          Hostname changed. Redeploy the app (push the repo, or use{' '}
          <span className="text-base-content">redeploy</span> on the latest deployment) to
          route it.
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
