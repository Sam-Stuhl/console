import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteSetting, fetchSettings, putSetting } from '../api/client'

export default function Settings() {
  const { data, isError, error } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  })

  const isSet = (key: string) => data?.set.includes(key) ?? false

  if (isError) {
    return <p className="font-mono text-xs text-error">{(error as Error).message}</p>
  }

  return (
    <div className="flex max-w-2xl flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="font-mono text-xl font-semibold">settings</h1>
        <p className="font-mono text-xs text-faint">
          Server-level credentials the console uses on your behalf. Stored
          encrypted, never shown again, and never in git.
        </p>
      </div>

      <Section
        title="github packages token"
        blurb={
          <>
            Lets the console pull <span className="text-base-content">private</span> app
            images from GHCR. Create a classic token with only the{' '}
            <span className="text-base-content">read:packages</span> scope, then paste it
            here. One token covers every private app.
          </>
        }
        link={{ href: 'https://github.com/settings/tokens/new', label: 'create a token on GitHub' }}
      >
        <SettingField
          keyName="ghcr_token"
          label="token"
          placeholder="ghp_… (read:packages)"
          isSet={isSet('ghcr_token')}
        />
      </Section>

      <Section
        title="cloudflare access"
        blurb={
          <>
            Lets a project's <span className="text-base-content">access</span> toggle put
            the Cloudflare login in front of an app. The API token needs the{' '}
            <span className="text-base-content">Account · Access: Apps and Policies · Edit</span>{' '}
            permission and nothing else.
          </>
        }
        link={{
          href: 'https://dash.cloudflare.com/profile/api-tokens',
          label: 'create a token on Cloudflare',
        }}
      >
        <SettingField
          keyName="cf_api_token"
          label="api token"
          placeholder="Cloudflare API token"
          isSet={isSet('cf_api_token')}
        />
        <SettingField
          keyName="cf_account_id"
          label="account id"
          placeholder="Cloudflare account id"
          isSet={isSet('cf_account_id')}
          secret={false}
        />
      </Section>
    </div>
  )
}

function Section({
  title,
  blurb,
  link,
  children,
}: {
  title: string
  blurb: React.ReactNode
  link: { href: string; label: string }
  children: React.ReactNode
}) {
  return (
    <section className="flex flex-col gap-3 border-t border-base-300 pt-4">
      <h2 className="font-mono text-xs text-muted">{title}</h2>
      <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">{blurb}</p>
      <a
        href={link.href}
        target="_blank"
        rel="noreferrer"
        className="self-start font-mono text-xs text-accent hover:underline"
      >
        {link.label} &#8599;
      </a>
      <div className="flex flex-col gap-3 pt-1">{children}</div>
    </section>
  )
}

function SettingField({
  keyName,
  label,
  placeholder,
  isSet,
  secret = true,
}: {
  keyName: string
  label: string
  placeholder: string
  isSet: boolean
  secret?: boolean
}) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState('')
  const [confirmClear, setConfirmClear] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  function refresh() {
    queryClient.invalidateQueries({ queryKey: ['settings'] })
  }

  const save = useMutation({
    mutationFn: () => putSetting(keyName, value),
    onSuccess: () => {
      setActionError(null)
      setValue('')
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const clear = useMutation({
    mutationFn: () => deleteSetting(keyName),
    onSuccess: () => {
      setActionError(null)
      setConfirmClear(false)
      refresh()
    },
    onError: (err: Error) => setActionError(err.message),
  })

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2 font-mono text-xs">
        <span className="w-24 text-muted">{label}</span>
        <span className={`h-1.5 w-1.5 rounded-full ${isSet ? 'bg-success' : 'bg-base-300'}`} />
        <span className={isSet ? 'text-faint' : 'text-faint'}>
          {isSet ? 'configured' : 'not set'}
        </span>
      </div>
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <input
          type={secret ? 'password' : 'text'}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={isSet ? 'enter a new value to replace' : placeholder}
          className="input input-sm w-80 max-w-full border-base-300 bg-base-100 font-mono text-xs"
        />
        <button
          type="submit"
          disabled={!value.trim() || save.isPending}
          className="btn btn-primary btn-sm font-mono"
        >
          save
        </button>
        {isSet &&
          (confirmClear ? (
            <span className="inline-flex items-center gap-3 font-mono text-xs">
              <button
                type="button"
                className="text-error/80 hover:text-error hover:underline"
                onClick={() => clear.mutate()}
              >
                confirm remove
              </button>
              <button
                type="button"
                className="text-muted hover:text-base-content"
                onClick={() => setConfirmClear(false)}
              >
                keep
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="font-mono text-xs text-error/80 transition-colors duration-150 hover:text-error hover:underline"
              onClick={() => setConfirmClear(true)}
            >
              remove
            </button>
          ))}
      </form>
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}
