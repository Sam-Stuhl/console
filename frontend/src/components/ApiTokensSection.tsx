import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  createApiToken,
  fetchApiTokens,
  revokeApiToken,
  type CreatedApiToken,
  type TokenScope,
} from '../api/client'
import { since } from '../lib/format'
import ConfirmButton from './ConfirmButton'

/**
 * Mint and revoke the tokens that authenticate the /v1 API and the MCP server.
 *
 * A token is shown exactly once, at creation: the console stores only a hash
 * and genuinely cannot show it again. That makes the copy-it-now panel the
 * important part of this UI rather than a nicety, so it stays on screen until
 * dismissed instead of appearing as a toast that can be missed.
 */
export default function ApiTokensSection({ origin }: { origin: string }) {
  const queryClient = useQueryClient()
  const { data: tokens, isLoading } = useQuery({
    queryKey: ['api-tokens'],
    queryFn: fetchApiTokens,
  })

  const [name, setName] = useState('')
  const [scope, setScope] = useState<TokenScope>('read')
  const [created, setCreated] = useState<CreatedApiToken | null>(null)

  const mint = useMutation({
    mutationFn: () => createApiToken(name.trim(), scope),
    onSuccess: (token) => {
      setCreated(token)
      setName('')
      queryClient.invalidateQueries({ queryKey: ['api-tokens'] })
    },
  })

  const revoke = useMutation({
    mutationFn: revokeApiToken,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-tokens'] }),
  })

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
        Tokens let a script or an AI agent reach this console without a browser
        login. A <span className="text-base-content">read</span> token can see
        everything; a <span className="text-base-content">write</span> token can
        also deploy, roll back, restart, and run commands. Neither can read a
        secret&apos;s value.
      </p>

      {created && <NewToken token={created} onDismiss={() => setCreated(null)} />}

      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          if (name.trim()) mint.mutate()
        }}
      >
        <label className="flex flex-col gap-1">
          <span className="font-mono text-xs text-muted">name</span>
          <input
            className="input input-sm input-bordered w-48 font-mono text-xs"
            placeholder="laptop, claude-code"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-mono text-xs text-muted">scope</span>
          <select
            className="select select-sm select-bordered font-mono text-xs"
            value={scope}
            onChange={(e) => setScope(e.target.value as TokenScope)}
          >
            <option value="read">read</option>
            <option value="write">write</option>
          </select>
        </label>
        <button
          type="submit"
          className="btn btn-sm btn-primary font-mono"
          disabled={!name.trim() || mint.isPending}
        >
          {mint.isPending ? 'minting…' : 'mint token'}
        </button>
      </form>

      {mint.isError && (
        <p className="font-mono text-xs text-error">{(mint.error as Error).message}</p>
      )}

      {isLoading ? (
        <div className="h-8 w-full animate-pulse rounded-sm bg-base-100" />
      ) : tokens && tokens.length > 0 ? (
        <table className="w-full font-mono text-xs">
          <thead className="text-muted">
            <tr className="border-b border-base-300">
              <th className="py-1 text-left font-normal">name</th>
              <th className="py-1 text-left font-normal">scope</th>
              <th className="py-1 text-left font-normal">token</th>
              <th className="py-1 text-left font-normal">last used</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {tokens.map((token) => (
              <tr key={token.id} className="border-b border-base-300/50">
                <td className="py-1.5 text-base-content">{token.name}</td>
                <td className="py-1.5">
                  <span
                    className={
                      token.scope === 'write' ? 'text-warning' : 'text-muted'
                    }
                  >
                    {token.scope}
                  </span>
                </td>
                <td className="py-1.5 text-faint">{token.preview}…</td>
                <td className="py-1.5 text-faint">
                  {token.last_used_at ? since(token.last_used_at) : 'never'}
                </td>
                <td className="py-1.5 text-right">
                  <ConfirmButton
                    label="revoke"
                    confirmLabel="really revoke"
                    onConfirm={() => revoke.mutate(token.id)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="font-mono text-xs text-faint">No tokens yet.</p>
      )}

      <ConnectAnAgent origin={origin} />
    </div>
  )
}

/** The one and only time the token is visible. */
function NewToken({
  token,
  onDismiss,
}: {
  token: CreatedApiToken
  onDismiss: () => void
}) {
  const [copied, setCopied] = useState(false)

  return (
    <div className="flex flex-col gap-2 rounded-box border border-warning/40 bg-warning/5 px-3 py-2">
      <p className="font-mono text-xs leading-relaxed text-warning">
        Copy this now. The console stores only a hash of it and cannot show it
        again. If you lose it, revoke it and mint another.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <code className="min-w-0 flex-1 break-all rounded-sm bg-base-100 px-2 py-1 font-mono text-xs text-base-content">
          {token.token}
        </code>
        <button
          type="button"
          className="btn btn-sm font-mono"
          onClick={() => {
            navigator.clipboard.writeText(token.token)
            setCopied(true)
          }}
        >
          {copied ? 'copied' : 'copy'}
        </button>
        <button type="button" className="btn btn-sm btn-ghost font-mono" onClick={onDismiss}>
          done
        </button>
      </div>
    </div>
  )
}

function ConnectAnAgent({ origin }: { origin: string }) {
  // The CLI command rather than a .mcp.json block: --scope user registers the
  // server for every project at once, and keeps the token in the user's own
  // config instead of a file that is designed to be committed.
  const addCommand = `claude mcp add --transport http --scope user console \\\n  ${origin}/mcp --header "Authorization: Bearer csk_your_token"`

  return (
    <details className="rounded-box border border-base-300 px-3 py-2">
      <summary className="cursor-pointer font-mono text-xs text-muted">
        connect an agent
      </summary>
      <div className="mt-3 flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <p className="font-mono text-xs text-faint">Claude Code:</p>
          <pre className="overflow-x-auto rounded-sm bg-base-100 px-2 py-1.5 font-mono text-xs text-base-content">
            {addCommand}
          </pre>
          <p className="font-mono text-[11px] leading-relaxed text-faint">
            <code className="text-base-content">--scope user</code> makes the
            console available in every project on this machine, and keeps the
            token out of any repo. Drop it to add the server to just the current
            project. Check it with{' '}
            <code className="text-base-content">claude mcp list</code>.
          </p>
        </div>
        <div className="flex flex-col gap-1">
          <p className="font-mono text-xs text-faint">From a shell:</p>
          <pre className="overflow-x-auto rounded-sm bg-base-100 px-2 py-1.5 font-mono text-xs text-base-content">
            {`curl -H "Authorization: Bearer csk_your_token" \\\n  ${origin}/v1/system`}
          </pre>
        </div>
        <p className="font-mono text-xs leading-relaxed text-faint">
          The full endpoint list is at{' '}
          <a className="link" href="/v1/docs" target="_blank" rel="noreferrer">
            /v1/docs
          </a>
          . Reaching either path from outside needs whatever gate fronts this
          console to let it through; see docs/api.md.
        </p>
      </div>
    </details>
  )
}
