import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  deleteSetting,
  disconnectGitHub,
  fetchBackups,
  fetchCredentials,
  fetchDeployments,
  fetchDomainsConfig,
  fetchGitHubStatus,
  fetchProjects,
  fetchSettings,
  pollGitHubDeviceFlow,
  putDomains,
  putSetting,
  runBackupNow,
  sendTestAlert,
  setCredentialExpiry,
  startGitHubDeviceFlow,
  type CredentialStatus,
  type DeviceFlow,
} from '../api/client'
import { copyText } from '../lib/clipboard'
import { formatBytes, since } from '../lib/format'
import {
  CollapsibleSection,
  ExpandCollapseAll,
  SectionsProvider,
  TableOfContents,
} from '../components/Sections'

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
    <SectionsProvider storageKey="console:settings-sections">
      <div className="flex items-start gap-10">
        <div className="flex min-w-0 max-w-2xl flex-1 flex-col gap-6">
          <div className="flex flex-col gap-2">
            <h1 className="font-mono text-xl font-semibold">settings</h1>
            <p className="font-mono text-xs text-faint">
              Credentials the console uses on your behalf. Stored encrypted, shown
              only as configured / not set, and never written to git.
            </p>
          </div>

          <CollapsibleSection id="github" title="github connection">
            <GitHubConnection />
          </CollapsibleSection>

          <CollapsibleSection id="ghcr" title="github packages token">
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
      </CollapsibleSection>

      <CollapsibleSection id="cloudflare" title="cloudflare access">
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
      </CollapsibleSection>

      <CollapsibleSection id="backups" title="backups">
        <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
          A nightly encrypted copy of the console&apos;s own state (its database
          and the <UI>Fernet key</UI>) pushed to a private GitHub repo. Lose the
          key and every stored secret is gone, so this is the one backup that
          matters. The bundle is encrypted with a <UI>passphrase</UI> you keep
          offline, so the repo alone reveals nothing.
        </p>

        <PassphraseField />
        <p className="font-mono text-[11px] text-faint">
          Prefer a mounted secret? A file at <Code>CONSOLE_BACKUP_PASSPHRASE_FILE</Code>{' '}
          is still honored and takes over if no passphrase is set here.
        </p>

        <Steps
          title="the destination repo"
          items={[
            <>
              Create a <UI>private</UI> GitHub repo just for backups, e.g.{' '}
              <Code>Sam-Stuhl/console-backups</Code>.
            </>,
            <>
              Make a token that can write to it: a fine-grained PAT scoped to
              that one repo with <UI>Contents: Read and write</UI> (a classic
              token with <UI>repo</UI> also works).
            </>,
            <>Paste the repo and token below.</>,
          ]}
          link={{
            href: 'https://github.com/settings/tokens?type=beta',
            label: 'open fine-grained tokens',
          }}
        />
        <SettingField
          keyName="backup_github_repo"
          label="repo"
          placeholder="owner/name"
          isSet={isSet('backup_github_repo')}
          secret={false}
        />
        <SettingField
          keyName="backup_github_token"
          label="token"
          placeholder="GitHub token (contents: write)"
          isSet={isSet('backup_github_token')}
        />

        <BackupPanel />
          </CollapsibleSection>

          <CollapsibleSection id="alerts" title="alerts">
            <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
              The console pushes a notification when an app stops answering its
              health check or comes back, and when a deploy fails, to an{' '}
              <UI>ntfy</UI> topic you subscribe to on your phone.
            </p>
            <Steps
              title="set up ntfy"
              items={[
                <>
                  Install the <UI>ntfy</UI> app (iOS or Android), or open{' '}
                  <Code>ntfy.sh</Code> in a browser.
                </>,
                <>
                  Pick a hard-to-guess topic name (anyone who knows it can read
                  your alerts) and subscribe to it in the app.
                </>,
                <>Paste that topic below and send a test.</>,
              ]}
              link={{ href: 'https://ntfy.sh', label: 'open ntfy.sh' }}
            />
            <SettingField
              keyName="ntfy_topic"
              label="topic"
              placeholder="e.g. samstuhl-console-7c2f"
              isSet={isSet('ntfy_topic')}
              secret={false}
            />
            <SettingField
              keyName="ntfy_server"
              label="server"
              placeholder="https://ntfy.sh (default)"
              isSet={isSet('ntfy_server')}
              secret={false}
            />
            <AlertTest disabled={!isSet('ntfy_topic')} />
          </CollapsibleSection>

          <CollapsibleSection id="domains" title="domains">
            <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
              Apps serve at <Code>{'{subdomain}.{domain}'}</Code>. Every project
              uses the primary domain out of the box; add more here to offer them
              when registering a project or when changing an existing one&apos;s
              domain. Adding a domain here only records it, so do the Cloudflare
              setup below first.
            </p>
            <Steps
              title="add a domain (once per domain)"
              items={[
                <>
                  Add the domain to Cloudflare as a zone: <UI>Add a site</UI>,
                  enter the domain, and switch its nameservers to Cloudflare at
                  your registrar. Wait until the zone shows <UI>Active</UI>.
                </>,
                <>
                  Route it through the same tunnel your other apps use:{' '}
                  <UI>
                    Zero Trust &rarr; Networks &rarr; Tunnels &rarr; your tunnel
                    &rarr; Public Hostname &rarr; Add a public hostname
                  </UI>
                  .
                </>,
                <>
                  Set <UI>Subdomain</UI> to <Code>*</Code>, <UI>Domain</UI> to the
                  new domain, <UI>Type</UI> to <Code>HTTP</Code>, and{' '}
                  <UI>URL</UI> to <Code>traefik:80</Code>, then save. The wildcard
                  covers every app&apos;s subdomain and creates the DNS for you.
                </>,
                <>
                  Add the domain below. It then appears in the project domain
                  picker and in each project&apos;s change-domain control. The
                  console never touches Cloudflare DNS or the tunnel itself.
                </>,
              ]}
              link={{
                href: 'https://one.dash.cloudflare.com',
                label: 'open Zero Trust',
              }}
            />
            <DomainsManager />
          </CollapsibleSection>

          <CollapsibleSection id="credential-expiry" title="credential expiry">
            <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
              Tokens expire and quietly break deploys or the access toggle. Set
              each one&apos;s expiry date and the console warns you (via your ntfy
              topic) before it lapses.
            </p>
            <CredentialExpiry />
          </CollapsibleSection>
        </div>

        <aside className="sticky top-20 hidden w-44 shrink-0 lg:block">
          <div className="mb-2 flex items-baseline justify-between gap-2">
            <p className="font-mono text-[11px] uppercase tracking-wide text-faint">
              on this page
            </p>
            <ExpandCollapseAll />
          </div>
          <TableOfContents />
        </aside>
      </div>
    </SectionsProvider>
  )
}

function DomainsManager() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['domains-config'], queryFn: fetchDomainsConfig })
  const [value, setValue] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (next: string[]) => putDomains(next),
    onSuccess: () => {
      setActionError(null)
      setValue('')
      queryClient.invalidateQueries({ queryKey: ['domains-config'] })
      // the create form and change-domain control read the available list
      queryClient.invalidateQueries({ queryKey: ['domains'] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (!data) return <span className="skeleton h-8 w-full max-w-md" />
  const extras = data.extras

  return (
    <div className="flex flex-col gap-2 pt-1">
      <ul className="flex flex-col gap-1.5 font-mono text-xs">
        <li className="flex items-center gap-2">
          <span className="h-1.5 w-1.5 rounded-full bg-success" />
          <span className="text-base-content">{data.primary}</span>
          <span className="rounded-sm bg-base-100 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-faint">
            primary
          </span>
        </li>
        {extras.map((d) => (
          <li key={d} className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-success" />
            <span className="text-base-content">{d}</span>
            <button
              type="button"
              disabled={save.isPending}
              onClick={() => save.mutate(extras.filter((x) => x !== d))}
              className="text-error/80 transition-colors duration-150 hover:text-error hover:underline disabled:opacity-40"
            >
              remove
            </button>
          </li>
        ))}
        {extras.length === 0 && <li className="text-faint">no extra domains yet.</li>}
      </ul>
      <form
        className="flex flex-wrap items-center gap-2 pt-1"
        onSubmit={(e) => {
          e.preventDefault()
          const d = value.trim()
          if (d) save.mutate([...extras, d])
        }}
      >
        <input
          type="text"
          value={value}
          spellCheck={false}
          onChange={(e) => setValue(e.target.value)}
          placeholder="apps.example.com"
          className="input input-sm w-80 max-w-full border-base-300 bg-base-100 font-mono text-xs"
        />
        <button
          type="submit"
          disabled={!value.trim() || save.isPending}
          className="btn btn-primary btn-sm font-mono"
        >
          add
        </button>
      </form>
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}

function PassphraseField() {
  const queryClient = useQueryClient()
  const { data } = useQuery({ queryKey: ['backups'], queryFn: fetchBackups })
  const configured = data?.passphrase ?? false

  const [value, setValue] = useState('')
  const [confirmed, setConfirmed] = useState(false)
  const [replacing, setReplacing] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => putSetting('backup_passphrase', value),
    onSuccess: () => {
      setActionError(null)
      setValue('')
      setConfirmed(false)
      setReplacing(false)
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      queryClient.invalidateQueries({ queryKey: ['backups'] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  function generate() {
    const bytes = new Uint8Array(24)
    crypto.getRandomValues(bytes)
    const b64 = btoa(String.fromCharCode(...bytes))
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '')
    setValue(b64)
    setConfirmed(false)
  }

  const showForm = !configured || replacing

  return (
    <div className="flex flex-col gap-2 pt-1">
      <div className="flex items-center gap-2 font-mono text-xs">
        <span className="w-24 text-muted">passphrase</span>
        <span className={`h-1.5 w-1.5 rounded-full ${configured ? 'bg-success' : 'bg-base-300'}`} />
        <span className="text-faint">{configured ? 'configured' : 'not set'}</span>
        {configured && !replacing && (
          <button
            type="button"
            className="ml-2 text-muted transition-colors duration-150 hover:text-base-content hover:underline"
            onClick={() => setReplacing(true)}
          >
            replace
          </button>
        )}
      </div>

      {showForm && (
        <>
          <Callout>
            Copy this into your password manager now. It is never shown again and
            cannot be recovered, and without it a backup can never be restored.
            {configured && ' Replacing it leaves existing backups needing the old passphrase.'}
          </Callout>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={value}
              spellCheck={false}
              onChange={(e) => {
                setValue(e.target.value)
                setConfirmed(false)
              }}
              placeholder="paste a passphrase, or generate one"
              className="input input-sm w-80 max-w-full border-base-300 bg-base-100 font-mono text-xs"
            />
            <button
              type="button"
              onClick={generate}
              className="rounded-field border border-base-300 px-3 py-1.5 font-mono text-xs text-muted transition-colors duration-150 hover:border-primary/50 hover:text-primary"
            >
              generate
            </button>
          </div>
          <label className="flex items-center gap-2 font-mono text-xs text-faint">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
              className="checkbox checkbox-xs"
            />
            I&apos;ve saved this passphrase in my password manager
          </label>
          <div className="flex items-center gap-3">
            <button
              type="button"
              disabled={!value.trim() || !confirmed || save.isPending}
              onClick={() => save.mutate()}
              className="btn btn-primary btn-sm font-mono"
            >
              save passphrase
            </button>
            {configured && replacing && (
              <button
                type="button"
                className="font-mono text-xs text-muted transition-colors duration-150 hover:text-base-content"
                onClick={() => {
                  setReplacing(false)
                  setValue('')
                  setConfirmed(false)
                }}
              >
                cancel
              </button>
            )}
          </div>
          {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
        </>
      )}
    </div>
  )
}

const EXPIRY_TONE: Record<string, string> = {
  ok: 'text-muted',
  warn: 'text-warning',
  expired: 'text-error',
  none: 'text-faint',
}
const EXPIRY_DOT: Record<string, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  expired: 'bg-error',
  none: 'bg-base-300',
}

function expiryLabel(c: CredentialStatus): string {
  if (c.days_left === null) return 'no date set'
  if (c.days_left < 0) return `expired ${-c.days_left}d ago`
  return `expires in ${c.days_left}d`
}

function CredentialExpiry() {
  const { data } = useQuery({ queryKey: ['credentials'], queryFn: fetchCredentials })
  if (!data) return <span className="skeleton h-8 w-full max-w-md" />
  return (
    <table className="w-full max-w-2xl font-mono text-xs">
      <tbody>
        {data.map((c) => (
          <CredentialRow key={c.key} cred={c} />
        ))}
      </tbody>
    </table>
  )
}

function CredentialRow({ cred }: { cred: CredentialStatus }) {
  const queryClient = useQueryClient()
  const [value, setValue] = useState(cred.expires_at ?? '')
  const [actionError, setActionError] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: (date: string | null) => setCredentialExpiry(cred.key, date),
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['credentials'] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const dirty = (value || null) !== (cred.expires_at ?? null)

  return (
    <tr className="border-b border-base-300/40 last:border-none align-top">
      <td className="py-2 pr-4">
        <div className="flex flex-col">
          <span>{cred.label}</span>
          <span className="text-faint">{cred.set ? 'configured' : 'not set'}</span>
        </div>
      </td>
      <td className="py-2 pr-3">
        <input
          type="date"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          className="input input-xs border-base-300 bg-base-100 font-mono text-xs"
        />
      </td>
      <td className="py-2 pr-4">
        {dirty && (
          <button
            type="button"
            disabled={save.isPending}
            onClick={() => save.mutate(value || null)}
            className="text-accent transition-colors duration-150 hover:underline disabled:opacity-40"
          >
            save
          </button>
        )}
      </td>
      <td className="py-2 text-right">
        <span className="inline-flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${EXPIRY_DOT[cred.state]}`} />
          <span className={EXPIRY_TONE[cred.state]}>{expiryLabel(cred)}</span>
        </span>
        {actionError && <div className="text-error">{actionError}</div>}
      </td>
    </tr>
  )
}

function AlertTest({ disabled }: { disabled: boolean }) {
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const test = useMutation({
    mutationFn: sendTestAlert,
    onSuccess: () => setResult({ ok: true, text: 'sent — check your phone' }),
    onError: (err: Error) => setResult({ ok: false, text: err.message }),
  })
  return (
    <div className="flex items-center gap-3 pt-1">
      <button
        type="button"
        disabled={disabled || test.isPending}
        onClick={() => test.mutate()}
        className="rounded-field border border-base-300 px-3 py-1.5 font-mono text-xs text-muted transition-colors duration-150 hover:border-primary/50 hover:text-primary disabled:opacity-40"
      >
        {test.isPending ? 'sending…' : 'send test'}
      </button>
      {result && (
        <span className={`font-mono text-xs ${result.ok ? 'text-faint' : 'text-error'}`}>
          {result.text}
        </span>
      )}
    </div>
  )
}

function BackupPanel() {
  const queryClient = useQueryClient()
  const [actionError, setActionError] = useState<string | null>(null)

  const { data, refetch } = useQuery({
    queryKey: ['backups'],
    queryFn: fetchBackups,
    // Poll while a backup is in flight, otherwise sit still.
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.status === 'running') ? 2000 : false,
  })

  // The destination status is derived from settings on the server, so refetch
  // it whenever the settings change (e.g. the repo/token were just saved).
  const settings = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  useEffect(() => {
    refetch()
  }, [settings.data, refetch])

  const backupNow = useMutation({
    mutationFn: runBackupNow,
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['backups'] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  return (
    <div className="flex flex-col gap-3 pt-1">
      <div className="flex flex-wrap items-center gap-4 font-mono text-xs">
        <Dot on={data?.passphrase ?? false} label="passphrase" />
        <Dot on={data?.destination ?? false} label="destination" />
        <button
          type="button"
          disabled={!data?.ready || backupNow.isPending}
          onClick={() => backupNow.mutate()}
          className="rounded-field border border-base-300 px-3 py-1 text-muted transition-colors duration-150 hover:border-primary/50 hover:text-primary disabled:opacity-40"
        >
          {backupNow.isPending ? 'starting…' : 'back up now'}
        </button>
      </div>
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
      {data && data.runs.length > 0 ? (
        <table className="w-full font-mono text-xs">
          <tbody>
            {data.runs.map((r) => (
              <tr key={r.id} className="border-b border-base-300/40 last:border-none">
                <td className="w-32 py-2 pr-4">
                  <span className="inline-flex items-center gap-2">
                    <span
                      className={`size-1.5 rounded-full ${
                        r.status === 'succeeded'
                          ? 'bg-success'
                          : r.status === 'failed'
                            ? 'bg-error'
                            : 'bg-warning motion-safe:animate-pulse'
                      }`}
                    />
                    <span className={r.status === 'failed' ? 'text-error' : 'text-muted'}>
                      {r.status}
                    </span>
                  </span>
                </td>
                <td className="w-16 py-2 pr-4 text-muted">{r.trigger}</td>
                <td className="w-20 py-2 pr-4 text-muted">
                  {r.size_bytes !== null ? formatBytes(r.size_bytes) : ''}
                </td>
                <td className="py-2 pr-4 text-muted" title={r.failure_reason ?? ''}>
                  {r.status === 'failed' ? (
                    <span className="text-error/80">{r.failure_reason}</span>
                  ) : (
                    since(r.created_at)
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="font-mono text-xs text-faint">no backups yet.</p>
      )}
    </div>
  )
}

function Dot({ on, label }: { on: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-1.5 w-1.5 rounded-full ${on ? 'bg-success' : 'bg-base-300'}`} />
      <span className="text-faint">{label}</span>
    </span>
  )
}

/* Connect a GitHub account through the OAuth device flow: the console shows a
   code, you approve it on github.com, and it polls until the token arrives.
   The token is outbound only. It lets the console read your repo list and a
   repo's console.toml; it grants nobody access to this console, which is still
   Cloudflare Access's job alone. */
function GitHubConnection() {
  const queryClient = useQueryClient()
  const { data: status } = useQuery({
    queryKey: ['github-status'],
    queryFn: fetchGitHubStatus,
  })
  const [flow, setFlow] = useState<DeviceFlow | null>(null)
  const [outcome, setOutcome] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [confirmDisconnect, setConfirmDisconnect] = useState(false)

  const start = useMutation({
    mutationFn: startGitHubDeviceFlow,
    onSuccess: (started) => {
      setOutcome(null)
      setFlow(started)
    },
    onError: (err: Error) => setOutcome(err.message),
  })

  const disconnect = useMutation({
    mutationFn: disconnectGitHub,
    onSuccess: () => {
      setConfirmDisconnect(false)
      queryClient.invalidateQueries({ queryKey: ['github-status'] })
      queryClient.invalidateQueries({ queryKey: ['github-repos'] })
    },
  })

  // Poll at the interval GitHub advertised, no faster: polling too often is
  // what earns a slow_down.
  useEffect(() => {
    if (!flow) return
    let cancelled = false
    const timer = window.setInterval(async () => {
      try {
        const { status: state } = await pollGitHubDeviceFlow(flow.device_code)
        if (cancelled || state === 'pending') return
        setFlow(null)
        if (state === 'connected') {
          queryClient.invalidateQueries({ queryKey: ['github-status'] })
          queryClient.invalidateQueries({ queryKey: ['github-repos'] })
        } else {
          setOutcome(
            state === 'denied'
              ? 'that request was denied on github.'
              : 'that code expired before it was approved.',
          )
        }
      } catch (err) {
        if (cancelled) return
        setFlow(null)
        setOutcome((err as Error).message)
      }
    }, flow.interval * 1000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [flow, queryClient])

  return (
    <>
      <p className="max-w-prose font-mono text-xs leading-relaxed text-faint">
        Lets you pick a repo when registering a project instead of typing one,
        and lets the console read a repo&apos;s <Code>console.toml</Code> when
        you deploy an image yourself. This is a credential the console uses to
        call GitHub; it gives nobody access to the console.
      </p>

      {status && !status.client_configured && (
        <>
          <Callout>
            No OAuth client id is set, so there is nothing to connect to yet.
          </Callout>
          <Steps
            title="one-time setup"
            items={[
              <>
                Open{' '}
                <UI>
                  GitHub &rarr; Settings &rarr; Developer settings &rarr; OAuth
                  Apps
                </UI>{' '}
                and click <UI>New OAuth App</UI>.
              </>,
              <>
                Name it something like <Code>my console</Code>. The homepage and
                callback URLs are not used by the device flow; your console&apos;s
                own URL is a fine answer for both.
              </>,
              <>
                On the app&apos;s page, tick <UI>Enable Device Flow</UI> and save.
              </>,
              <>
                Copy the <UI>Client ID</UI> and set{' '}
                <Code>CONSOLE_GITHUB_CLIENT_ID</Code> in the console&apos;s
                environment, then restart it.
              </>,
            ]}
            link={{
              href: 'https://github.com/settings/developers',
              label: 'open the oauth apps page',
            }}
          />
        </>
      )}

      {status?.connected && status.error && (
        <Callout>
          {status.error} The connection below no longer works.
        </Callout>
      )}

      {status?.connected ? (
        <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
          <span className="text-muted">connected as</span>
          <span className="text-base-content">{status.login ?? 'unknown'}</span>
          {confirmDisconnect ? (
            <span className="inline-flex items-center gap-3">
              <button
                type="button"
                className="text-warning hover:underline"
                onClick={() => disconnect.mutate()}
              >
                confirm
              </button>
              <button
                type="button"
                className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                onClick={() => setConfirmDisconnect(false)}
              >
                keep
              </button>
            </span>
          ) : (
            <button
              type="button"
              className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
              onClick={() => setConfirmDisconnect(true)}
            >
              disconnect
            </button>
          )}
          <span className="text-faint">
            disconnecting forgets the token here; revoke it on github to end the
            authorization itself
          </span>
        </div>
      ) : flow ? (
        <div className="flex flex-col gap-2 font-mono text-xs">
          <p className="text-muted">
            enter this code on github, then come back. this page is waiting.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <span className="rounded-sm bg-base-100 px-2 py-1 text-base tracking-[0.3em] text-base-content">
              {flow.user_code}
            </span>
            <button
              type="button"
              className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
              onClick={async () => {
                await copyText(flow.user_code)
                setCopied(true)
                window.setTimeout(() => setCopied(false), 2000)
              }}
            >
              {copied ? 'copied' : 'copy'}
            </button>
            <a
              href={flow.verification_uri}
              target="_blank"
              rel="noreferrer"
              className="text-accent hover:underline"
            >
              open github &#8599;
            </a>
            <button
              type="button"
              className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
              onClick={() => setFlow(null)}
            >
              cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          disabled={!status?.client_configured || start.isPending}
          className="btn btn-primary btn-sm self-start font-mono"
          onClick={() => start.mutate()}
        >
          {start.isPending ? 'starting…' : 'connect github'}
        </button>
      )}

      {outcome && <p className="font-mono text-xs text-error">{outcome}</p>}
    </>
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
