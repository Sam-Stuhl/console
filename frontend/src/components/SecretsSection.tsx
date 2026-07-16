import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ImportResult } from '../api/client'
import {
  deleteSecret,
  exportSecrets,
  fetchDeployments,
  importSecrets,
  fetchSecrets,
  putSecret,
  redeployDeployment,
  revealSecret,
} from '../api/client'
import { canRedeploy } from './DeploymentsSection'
import DropTextArea from './DropTextArea'
import { copyText } from '../lib/clipboard'
const KEY_PATTERN = '[A-Z_][A-Z0-9_]*'

export default function SecretsSection({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const { data: secrets, isError, error } = useQuery({
    queryKey: ['secrets', projectId],
    queryFn: () => fetchSecrets(projectId),
  })

  const [revealed, setRevealed] = useState<Record<string, string>>({})
  const [editing, setEditing] = useState<string | null>(null)
  const [editValue, setEditValue] = useState('')
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [importing, setImporting] = useState(false)
  const [importText, setImportText] = useState('')
  const [importResult, setImportResult] = useState<ImportResult | null>(null)
  const [copied, setCopied] = useState(false)
  // True once a secret has been changed this session but not yet redeployed, so
  // we can nudge that the running app is still on the old values.
  const [changed, setChanged] = useState(false)

  // Shares the deployments cache with DeploymentsSection. The latest deployable
  // build is what a redeploy re-runs to pick up the new secrets.
  const { data: deployments } = useQuery({
    queryKey: ['deployments', projectId],
    queryFn: () => fetchDeployments(projectId),
  })
  const latest = deployments?.[0]
  const redeployable = latest ? canRedeploy(latest) : false

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['secrets', projectId] })
  }

  const redeploy = useMutation({
    mutationFn: () => redeployDeployment(projectId, latest!.id),
    onSuccess: () => {
      setActionError(null)
      setChanged(false)
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const save = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) =>
      putSecret(projectId, key, value),
    onSuccess: (_data, { key }) => {
      setActionError(null)
      setEditing(null)
      setEditValue('')
      setNewKey('')
      setNewValue('')
      setRevealed(({ [key]: _gone, ...rest }) => rest)
      setChanged(true)
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const remove = useMutation({
    mutationFn: (key: string) => deleteSecret(projectId, key),
    onSuccess: (_data, key) => {
      setActionError(null)
      setRevealed(({ [key]: _gone, ...rest }) => rest)
      setChanged(true)
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const reveal = useMutation({
    mutationFn: (key: string) => revealSecret(projectId, key),
    onSuccess: (data) => {
      setActionError(null)
      setRevealed((r) => ({ ...r, [data.key]: data.value }))
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const doImport = useMutation({
    mutationFn: () => importSecrets(projectId, importText),
    onSuccess: (result) => {
      setActionError(null)
      setImportResult(result)
      setImporting(false)
      setImportText('')
      setRevealed({})
      if (result.added.length > 0 || result.updated.length > 0) setChanged(true)
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const doExport = useMutation({
    mutationFn: () => exportSecrets(projectId),
    onSuccess: async (data) => {
      setActionError(null)
      await copyText(data.env)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (isError) {
    return <p className="font-mono text-xs text-error">{(error as Error).message}</p>
  }
  if (!secrets) {
    return <span className="skeleton h-8 w-full max-w-md" />
  }

  return (
    <div className="flex max-w-3xl flex-col gap-3">
      {secrets.length === 0 && (
        <p className="font-mono text-xs text-faint">
          no secrets stored. names declared in console.toml must exist here
          before a deploy is allowed to start.
        </p>
      )}

      {secrets.length > 0 && (
        <table className="w-full font-mono text-xs">
          <tbody>
            {secrets.map((s) => (
              <tr key={s.key} className="border-b border-base-300/40 last:border-none">
                <td className="w-56 py-2 pr-4 align-top">{s.key}</td>
                <td className="py-2 pr-4">
                  {editing === s.key ? (
                    <form
                      className="flex items-center gap-2"
                      onSubmit={(e) => {
                        e.preventDefault()
                        save.mutate({ key: s.key, value: editValue })
                      }}
                    >
                      <input
                        autoFocus
                        type="password"
                        value={editValue}
                        onChange={(e) => setEditValue(e.target.value)}
                        placeholder="new value"
                        className="input input-xs w-full border-base-300 bg-base-100 font-mono"
                      />
                      <button type="submit" className="btn btn-primary btn-xs font-mono">
                        save
                      </button>
                      <button
                        type="button"
                        className="btn btn-ghost btn-xs font-mono text-muted"
                        onClick={() => setEditing(null)}
                      >
                        cancel
                      </button>
                    </form>
                  ) : revealed[s.key] !== undefined ? (
                    <span className="break-all">{revealed[s.key]}</span>
                  ) : (
                    <span className="text-faint">••••••••</span>
                  )}
                </td>
                <td className="w-44 py-2 text-right align-top whitespace-nowrap">
                  {editing !== s.key && (
                    <span className="inline-flex items-center gap-3">
                      {revealed[s.key] !== undefined ? (
                        <ActionLink
                          label="hide"
                          onClick={() =>
                            setRevealed(({ [s.key]: _gone, ...rest }) => rest)
                          }
                        />
                      ) : (
                        <ActionLink label="reveal" onClick={() => reveal.mutate(s.key)} />
                      )}
                      <ActionLink
                        label="edit"
                        onClick={() => {
                          setEditing(s.key)
                          setEditValue('')
                        }}
                      />
                      {confirmingDelete === s.key ? (
                        <>
                          <ActionLink
                            label="confirm"
                            danger
                            onClick={() => {
                              setConfirmingDelete(null)
                              remove.mutate(s.key)
                            }}
                          />
                          <ActionLink label="keep" onClick={() => setConfirmingDelete(null)} />
                        </>
                      ) : (
                        <ActionLink
                          label="delete"
                          danger
                          onClick={() => setConfirmingDelete(s.key)}
                        />
                      )}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex items-center gap-4 font-mono text-xs">
        {importing ? (
          <ActionLink
            label="cancel import"
            onClick={() => {
              setImporting(false)
              setImportText('')
            }}
          />
        ) : (
          <ActionLink
            label="import .env"
            onClick={() => {
              setImportResult(null)
              setImporting(true)
            }}
          />
        )}
        {secrets.length > 0 &&
          (copied ? (
            <span className="text-success">copied</span>
          ) : (
            <ActionLink label="copy as .env" onClick={() => doExport.mutate()} />
          ))}
      </div>

      {importing && (
        <div className="flex flex-col gap-2">
          <DropTextArea
            value={importText}
            onChange={setImportText}
            placeholder={'paste .env contents here, or drop the file onto this box\n\nDATABASE_URL=postgres://...\nAPI_KEY="..."'}
          />
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={doImport.isPending || !importText.trim()}
              onClick={() => doImport.mutate()}
              className="btn btn-primary btn-sm font-mono"
            >
              import
            </button>
            <span className="font-mono text-xs text-faint">
              existing keys with the same name are overwritten
            </span>
          </div>
        </div>
      )}

      {importResult && (
        <div className="flex flex-col gap-1 font-mono text-xs">
          <p className="text-muted">
            imported: {importResult.added.length} added
            {importResult.added.length > 0 && ` (${importResult.added.join(', ')})`}
            , {importResult.updated.length} updated
            {importResult.updated.length > 0 &&
              ` (${importResult.updated.join(', ')})`}
          </p>
          {importResult.skipped.map((reason) => (
            <p key={reason} className="text-warning">
              skipped {reason}
            </p>
          ))}
        </div>
      )}

      <form
        className="flex flex-wrap items-center gap-2 pt-1"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate({ key: newKey, value: newValue })
        }}
      >
        <input
          value={newKey}
          onChange={(e) => setNewKey(e.target.value.toUpperCase())}
          placeholder="KEY_NAME"
          pattern={KEY_PATTERN}
          required
          className="input input-sm w-44 border-base-300 bg-base-100 font-mono text-xs"
        />
        <input
          type="password"
          value={newValue}
          onChange={(e) => setNewValue(e.target.value)}
          placeholder="value"
          required
          className="input input-sm w-64 border-base-300 bg-base-100 font-mono text-xs"
        />
        <button
          type="submit"
          disabled={save.isPending}
          className="btn btn-primary btn-sm font-mono"
        >
          add secret
        </button>
      </form>

      <RedeployNote
        hasDeploy={(deployments?.length ?? 0) > 0}
        changed={changed}
        redeployable={redeployable}
        pending={redeploy.isPending}
        onRedeploy={() => redeploy.mutate()}
      />

      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}

function RedeployNote({
  hasDeploy,
  changed,
  redeployable,
  pending,
  onRedeploy,
}: {
  hasDeploy: boolean
  changed: boolean
  redeployable: boolean
  pending: boolean
  onRedeploy: () => void
}) {
  // Secrets are read when the container starts, so a change only reaches the
  // running app on its next deploy. Nudge that, and offer the redeploy inline.
  const nudge = hasDeploy && changed
  const text = !hasDeploy
    ? 'Secrets apply on the app’s first deploy.'
    : changed
      ? 'Secret changed. Redeploy to apply it to the running app.'
      : 'Secrets are read at container start, so changes take effect on the next deploy.'

  return (
    <div
      className={`flex flex-wrap items-center gap-3 rounded-box border px-3 py-2 font-mono text-xs leading-relaxed ${
        nudge ? 'border-warning/40 bg-warning/5 text-warning' : 'border-base-300 text-faint'
      }`}
    >
      <span>{text}</span>
      {hasDeploy &&
        (redeployable ? (
          <button
            type="button"
            disabled={pending}
            onClick={onRedeploy}
            className="rounded-field border border-base-300 px-2.5 py-1 text-muted transition-colors duration-150 hover:border-primary/50 hover:text-primary disabled:opacity-40"
          >
            {pending ? 'redeploying…' : 'redeploy'}
          </button>
        ) : (
          <span className="text-faint">a deploy is in progress; redeploy when it finishes</span>
        ))}
    </div>
  )
}

function ActionLink({
  label,
  onClick,
  danger = false,
}: {
  label: string
  onClick: () => void
  danger?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`transition-colors duration-150 hover:underline ${
        danger ? 'text-error/80 hover:text-error' : 'text-muted hover:text-base-content'
      }`}
    >
      {label}
    </button>
  )
}
