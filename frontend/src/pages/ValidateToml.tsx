import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import type { TomlValidation } from '../api/client'
import { validateConsoleToml } from '../api/client'
import DropTextArea from '../components/DropTextArea'

export default function ValidateToml() {
  const [text, setText] = useState('')
  const [result, setResult] = useState<TomlValidation | null>(null)

  const check = useMutation({
    mutationFn: () => validateConsoleToml(text),
    onSuccess: setResult,
  })

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <div className="flex flex-col gap-2">
        <h1 className="font-mono text-xl font-semibold">toml check</h1>
        <p className="font-mono text-xs text-muted">
          paste a repo&apos;s console.toml (or drop the file) to run it through
          the same validator a deploy uses. green here means it will not fail
          a deploy on validation.
        </p>
      </div>

      <DropTextArea
        value={text}
        onChange={(t) => {
          setText(t)
          setResult(null)
        }}
        placeholder={'secrets = ["DATABASE_URL"]\n\n[app]\nname = "demo"\nsubdomain = "demo"\nport = 8080'}
        rows={14}
      />

      <div>
        <button
          type="button"
          disabled={check.isPending || !text.trim()}
          onClick={() => check.mutate()}
          className="btn btn-primary btn-sm font-mono"
        >
          validate
        </button>
      </div>

      {check.isError && (
        <p className="font-mono text-xs text-error">{(check.error as Error).message}</p>
      )}

      {result && !result.valid && (
        <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-xs text-error">
          {result.error}
        </div>
      )}

      {result?.valid && result.summary && (
        <div className="flex flex-col gap-3">
          <span className="inline-flex items-center gap-2 font-mono text-xs">
            <span aria-hidden className="size-1.5 rounded-full bg-success" />
            valid
          </span>
          {result.warnings.map((w) => (
            <p key={w} className="font-mono text-xs text-warning">
              warning: {w}
            </p>
          ))}
          <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
            <Row label="app" value={result.summary.name} />
            <Row
              label="url"
              value={`${result.summary.subdomain}.samstuhl.com -> port ${result.summary.port}`}
            />
            <Row label="health" value={result.summary.health} />
            <Row label="resources" value={result.summary.resources} />
            <Row
              label="env"
              value={result.summary.env_keys.join(', ') || '(none)'}
            />
            <Row
              label="secrets"
              value={result.summary.secrets.join(', ') || '(none)'}
            />
          </dl>
        </div>
      )}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="font-mono text-xs leading-6 text-muted">{label}</dt>
      <dd className="font-mono text-xs leading-6">{value}</dd>
    </>
  )
}
