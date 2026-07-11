import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteSetting,
  fetchDeployments,
  fetchProjects,
  fetchSettings,
  putSetting,
} from '../api/client'

export default function Settings() {
  const { data, isError, error } = useQuery({
    queryKey: ['settings'],
    queryFn: fetchSettings,
  })

  // Tie the GHCR token to real deploy failures: find apps whose latest deploy
  // failed on an unauthorized (private image) pull.
  const { data: apps } = useQuery({
    queryKey: ['settings-app-state'],
    queryFn: async () => {
      const projects = await fetchProjects()
      return Promise.all(
        projects.map(async (p) => ({
          name: p.name,
          latest: (await fetchDeployments(p.id))[0] ?? null,
        })),
      )
    },
  })

  const isSet = (key: string) => data?.set.includes(key) ?? false
  const pullBlocked = (apps ?? [])
    .filter(
      (a) =>
        a.latest?.status === 'failed' &&
        (a.latest.failure_reason ?? '').toLowerCase().includes('unauthorized'),
    )
    .map((a) => a.name)

  if (isError) {
    return <p className="font-mono text-xs text-error">{(error as Error).message}</p>
  }

  return (
    <div className="flex max-w-2xl flex-col gap-8">
      <div className="flex flex-col gap-2">
        <h1 className="font-mono text-xl font-semibold">settings</h1>
        <p className="font-mono text-xs text-faint">
          Credentials the console uses on your behalf. Stored encrypted, shown
          only as configured / not set, and never written to git.
        </p>
      </div>

      <Section title="github packages token">
        <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
          The console pulls each app&apos;s image from GHCR. A public repo&apos;s
          image is public; a <UI>private</UI> repo&apos;s image is private and
          needs a read token. One token covers every private app.
        </p>

        {pullBlocked.length > 0 && (
          <Callout>
            {pullBlocked.join(', ')} failed its last deploy because the image is
            private.{' '}
            {isSet('ghcr_token')
              ? 'The token is set, so re-run that deploy (push the repo again) to retry.'
              : 'Add a token below, then push the repo again to retry.'}
          </Callout>
        )}

        <Steps
          items={[
            <>
              Open{' '}
              <UI>
                GitHub &rarr; Settings &rarr; Developer settings &rarr; Personal
                access tokens &rarr; Tokens (classic)
              </UI>{' '}
              and click <UI>Generate new token (classic)</UI>.
            </>,
            <>
              Give it a note like <Code>console GHCR read</Code> and an
              expiration (or <UI>No expiration</UI>).
            </>,
            <>
              Under <UI>Select scopes</UI>, check <UI>read:packages</UI> and
              nothing else.
            </>,
            <>
              Click <UI>Generate token</UI> and copy it (it starts with{' '}
              <Code>ghp_</Code>).
            </>,
            <>Paste it below and save.</>,
          ]}
          link={{
            href: 'https://github.com/settings/tokens/new',
            label: 'open the token page',
          }}
        />

        <SettingField
          keyName="ghcr_token"
          label="token"
          placeholder="ghp_… (read:packages)"
          isSet={isSet('ghcr_token')}
        />
      </Section>

      <Section title="cloudflare access">
        <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
          Lets a project&apos;s <UI>access</UI> toggle put the Cloudflare login
          in front of an app. Needs an API token plus your account id.
        </p>

        <Steps
          title="the api token"
          items={[
            <>
              Open Cloudflare <UI>&rarr; My Profile &rarr; API Tokens &rarr;
              Create Token</UI>, and under <UI>Create Custom Token</UI> click{' '}
              <UI>Get started</UI>.
            </>,
            <>
              Name it <Code>console access</Code>.
            </>,
            <>
              Add exactly one permission:{' '}
              <UI>Account &middot; Access: Apps and Policies &middot; Edit</UI>.
            </>,
            <>
              Under <UI>Account Resources</UI>, Include your account.
            </>,
            <>
              <UI>Continue to summary &rarr; Create Token</UI>, copy it, paste it
              below.
            </>,
          ]}
          link={{
            href: 'https://dash.cloudflare.com/profile/api-tokens',
            label: 'open API tokens',
          }}
        />
        <SettingField
          keyName="cf_api_token"
          label="api token"
          placeholder="Cloudflare API token"
          isSet={isSet('cf_api_token')}
        />

        <Steps
          title="the account id"
          items={[
            <>
              In the Cloudflare dashboard, open any site&apos;s <UI>Overview</UI>.
              The <UI>Account ID</UI> is in the right sidebar under <UI>API</UI>{' '}
              (it&apos;s also the long id in the dashboard URL).
            </>,
            <>Copy it and paste below.</>,
          ]}
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

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-base-300 pt-4">
      <h2 className="font-mono text-xs text-muted">{title}</h2>
      {children}
    </section>
  )
}

// A UI label the user will see on the other site — brighter so steps are
// skimmable.
function UI({ children }: { children: React.ReactNode }) {
  return <span className="text-base-content">{children}</span>
}

function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="rounded-sm bg-base-100 px-1 py-0.5 text-base-content">{children}</code>
  )
}

function Steps({
  title,
  items,
  link,
}: {
  title?: string
  items: React.ReactNode[]
  link?: { href: string; label: string }
}) {
  return (
    <div className="flex flex-col gap-1.5">
      {title && (
        <p className="font-mono text-[11px] uppercase tracking-wide text-muted">{title}</p>
      )}
      <ol className="flex list-decimal flex-col gap-1 pl-4 font-mono text-xs leading-relaxed text-faint marker:text-muted">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ol>
      {link && (
        <a
          href={link.href}
          target="_blank"
          rel="noreferrer"
          className="self-start font-mono text-xs text-accent hover:underline"
        >
          {link.label} &#8599;
        </a>
      )}
    </div>
  )
}

function Callout({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-box border border-warning/40 bg-warning/5 px-3 py-2 font-mono text-xs leading-relaxed text-warning">
      {children}
    </div>
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
    <div className="flex flex-col gap-1.5 pt-1">
      <div className="flex items-center gap-2 font-mono text-xs">
        <span className="w-24 text-muted">{label}</span>
        <span className={`h-1.5 w-1.5 rounded-full ${isSet ? 'bg-success' : 'bg-base-300'}`} />
        <span className="text-faint">{isSet ? 'configured' : 'not set'}</span>
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
