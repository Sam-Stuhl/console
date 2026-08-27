import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchProject, updateAccess } from '../api/client'
import AccessPathsSection from './AccessPathsSection'

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

export default function AccessSection({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const { data: project } = useQuery({
    queryKey: ['project', projectId],
    queryFn: () => fetchProject(projectId),
  })

  const [emails, setEmails] = useState<string[]>([])
  const [newEmail, setNewEmail] = useState('')
  const [confirmingPublic, setConfirmingPublic] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  // Re-seed the local list only when the server's list actually changes (after
  // a save), so it never clobbers what you are mid-edit.
  const serverEmails = project?.access_emails.join(',')
  useEffect(() => {
    if (project) setEmails(project.access_emails)
  }, [serverEmails]) // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (p: { isProtected: boolean; emails: string[] }) =>
      updateAccess(projectId, p.isProtected, p.emails),
    onSuccess: () => {
      setActionError(null)
      setConfirmingPublic(false)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (!project) return <span className="skeleton h-8 w-full max-w-md" />

  const protectedNow = project.protected
  const changed =
    emails.length !== project.access_emails.length ||
    emails.some((e, i) => e !== project.access_emails[i])
  const notConfigured = actionError?.toLowerCase().includes('not configured') ?? false

  function addEmail() {
    const value = newEmail.trim().toLowerCase()
    if (!EMAIL_RE.test(value)) {
      setActionError(`"${newEmail}" is not a valid email`)
      return
    }
    if (emails.includes(value)) {
      setNewEmail('')
      return
    }
    setActionError(null)
    setEmails([...emails, value])
    setNewEmail('')
  }

  return (
    <div className="flex max-w-2xl flex-col gap-3">
      <div className="flex items-center gap-2 font-mono text-xs">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            protectedNow ? 'bg-success' : 'bg-base-300'
          }`}
        />
        <span className={protectedNow ? '' : 'text-muted'}>
          {protectedNow ? 'login required' : 'public'}
        </span>
        <span className="text-faint">
          {protectedNow
            ? 'Cloudflare Access gates this app at the edge'
            : 'anyone with the link can reach this app'}
        </span>
      </div>

      {emails.length > 0 && (
        <ul className="flex flex-col font-mono text-xs">
          {emails.map((email) => (
            <li
              key={email}
              className="flex items-center justify-between border-b border-base-300/40 py-1.5 last:border-none"
            >
              <span>{email}</span>
              <button
                type="button"
                className="text-error/80 transition-colors duration-150 hover:text-error hover:underline"
                onClick={() => setEmails(emails.filter((e) => e !== email))}
              >
                remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          addEmail()
        }}
      >
        <input
          type="email"
          value={newEmail}
          onChange={(e) => setNewEmail(e.target.value)}
          placeholder="name@example.com"
          className="input input-sm w-64 border-base-300 bg-base-100 font-mono text-xs"
        />
        <button type="submit" className="btn btn-ghost btn-sm font-mono text-muted">
          add email
        </button>
      </form>

      <div className="flex flex-wrap items-center gap-4 pt-1 font-mono text-xs">
        {!protectedNow && (
          <button
            type="button"
            disabled={emails.length === 0 || save.isPending}
            onClick={() => save.mutate({ isProtected: true, emails })}
            className="btn btn-primary btn-sm font-mono"
          >
            require login
          </button>
        )}

        {protectedNow && (
          <>
            <button
              type="button"
              disabled={!changed || emails.length === 0 || save.isPending}
              onClick={() => save.mutate({ isProtected: true, emails })}
              className="btn btn-primary btn-sm font-mono"
            >
              update allowed emails
            </button>
            {confirmingPublic ? (
              <span className="inline-flex items-center gap-3">
                <span className="text-warning">expose to everyone?</span>
                <button
                  type="button"
                  className="text-error/80 hover:text-error hover:underline"
                  onClick={() => save.mutate({ isProtected: false, emails: [] })}
                >
                  confirm
                </button>
                <button
                  type="button"
                  className="text-muted hover:text-base-content"
                  onClick={() => setConfirmingPublic(false)}
                >
                  keep
                </button>
              </span>
            ) : (
              <button
                type="button"
                className="text-error/80 transition-colors duration-150 hover:text-error hover:underline"
                onClick={() => setConfirmingPublic(true)}
              >
                make public
              </button>
            )}
          </>
        )}
      </div>

      <div className="mt-2 flex flex-col gap-3 border-t border-base-300/40 pt-4">
        <p className="font-mono text-[11px] uppercase tracking-wide text-muted">
          paths without the login
        </p>
        <AccessPathsSection projectId={projectId} />
      </div>

      {notConfigured ? (
        <p className="font-mono text-xs text-warning">
          Cloudflare Access is not configured on the server, so the login gate
          cannot be set. Add CONSOLE_CF_ACCOUNT_ID and a scoped API token, then
          try again. The app stays public until then.
        </p>
      ) : (
        actionError && <p className="font-mono text-xs text-error">{actionError}</p>
      )}
    </div>
  )
}
