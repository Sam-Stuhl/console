import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ImportResult } from '../api/client'
import {
  deleteSecret,
  exportSecrets,
  fetchSecrets,
  importSecrets,
  putSecret,
  revealSecret,
} from '../api/client'
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

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['secrets', projectId] })
  }

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
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const remove = useMutation({
    mutationFn: (key: string) => deleteSecret(projectId, key),
    onSuccess: (_data, key) => {
      setActionError(null)
      setRevealed(({ [key]: _gone, ...rest }) => rest)
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

      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
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
