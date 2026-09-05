export interface Container {
  id: string
  name: string
  image: string
  state: string
  created_at: string
  started_at: string | null
  finished_at: string | null
  exit_code: number | null
}

export interface EnvVar {
  key: string
  value: string
}

export interface PortMapping {
  container_port: string
  host_ports: string[]
}

export interface ContainerDetail extends Container {
  env: EnvVar[]
  labels: Record<string, string>
  networks: string[]
  ports: PortMapping[]
  restart_policy: string
}

export interface Stats {
  cpu_percent: number
  mem_usage: number
  mem_limit: number
  mem_percent: number
}

export interface Project {
  id: string
  name: string
  repo: string
  branch: string
  subdomain: string
  created_at: string
  url: string
  protected: boolean
  access_emails: string[]
  health: string // live liveness from the monitor: up | down | unknown
  domain: string // base domain it serves under
  has_icon: boolean // the app's favicon has been fetched (else show initials)
  icon_fetched_at: string | null // cache-buster for the icon URL
  deploy_status: string | null // latest deployment status (queued/building/deploying/live/failed/…)
  is_live: boolean // a deployment is currently serving, independent of the monitor ping
  image_hint: string // what CI tags this project's images as, minus the tag
}

// The app's fetched favicon, served from the console. Includes the fetch time
// as a cache-buster so a refreshed icon shows without a hard reload.
export const projectIconUrl = (p: Project) =>
  `/api/projects/${p.id}/icon?v=${encodeURIComponent(p.icon_fetched_at ?? '')}`

export const refreshProjectIcon = (id: string) =>
  request<{ fetched: boolean }>(`/api/projects/${id}/icon/refresh`, jsonInit('POST'))

export interface SecretMeta {
  key: string
  updated_at: string
}

export interface DeploymentSummary {
  id: string
  sha: string
  commit_message: string | null
  image: string | null
  status: string
  substate: string | null
  run_url: string | null
  failure_reason: string | null
  created_at: string
  build_finished_at: string | null
  deploy_started_at: string | null
  finished_at: string | null
}

export interface DeploymentDetail extends DeploymentSummary {
  log: string | null
  config_snapshot: string | null
  container_name: string | null
  router_priority: number | null
}

export interface BackupRun {
  id: string
  trigger: string
  status: string
  location: string | null
  size_bytes: number | null
  failure_reason: string | null
  created_at: string
  finished_at: string | null
}

export interface BackupStatus {
  passphrase: boolean
  destination: boolean
  ready: boolean
  runs: BackupRun[]
}

export interface CommandRunSummary {
  id: string
  command: string
  container_name: string | null
  status: string
  exit_code: number | null
  failure_reason: string | null
  created_at: string
  finished_at: string | null
}

export interface CommandRunDetail extends CommandRunSummary {
  output: string | null
}

/* Carries the status alongside the message, so a caller can tell apart
   failures that read the same to a user but need different handling (a 503 for
   an unconfigured dependency versus a 400 for bad input). */
export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new ApiError(res.status, detail)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

const getJson = <T,>(url: string) => request<T>(url)

const jsonInit = (method: string, body?: unknown): RequestInit => ({
  method,
  headers: { 'content-type': 'application/json' },
  body: body === undefined ? undefined : JSON.stringify(body),
})

export const fetchContainers = () => getJson<Container[]>('/api/containers')

export const fetchContainer = (id: string) =>
  getJson<ContainerDetail>(`/api/containers/${id}`)

export const fetchStats = (id: string) =>
  getJson<Stats>(`/api/containers/${id}/stats`)

export async function fetchLogs(id: string, tail = 500): Promise<string> {
  const res = await fetch(`/api/containers/${id}/logs?tail=${tail}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.text()
}

export const fetchProjects = () => getJson<Project[]>('/api/projects')

export const fetchProject = (id: string) => getJson<Project>(`/api/projects/${id}`)

export const createProject = (body: {
  name: string
  repo: string
  branch: string
  subdomain: string
  domain?: string
}) => request<Project>('/api/projects', jsonInit('POST', body))

export const fetchDomains = () =>
  getJson<{ domains: string[] }>('/api/projects/domains')

export interface DomainsConfig {
  primary: string
  extras: string[]
}

export const fetchDomainsConfig = () => getJson<DomainsConfig>('/api/domains')

export const putDomains = (extras: string[]) =>
  request<DomainsConfig>('/api/domains', jsonInit('PUT', { extras }))

export type Repoint = 'auto' | 'manual'

export interface DomainChangeResult {
  project: Project
  redeploy_required: boolean
  note: string | null
}

export const changeProjectDomain = (
  id: string,
  domain: string | null,
  repoint: Repoint,
) =>
  request<DomainChangeResult>(
    `/api/projects/${id}/domain`,
    jsonInit('PUT', { domain, repoint }),
  )

export const deleteProject = (id: string) =>
  request<void>(`/api/projects/${id}`, jsonInit('DELETE'))

export const updateAccess = (id: string, isProtected: boolean, emails: string[]) =>
  request<Project>(
    `/api/projects/${id}/access`,
    jsonInit('PUT', { protected: isProtected, emails }),
  )

// A Cloudflare Access bypass: one path machines can reach without the login.
// Scoped either to a project's hostname or to the console's own.
export interface AccessPath {
  id: string
  path: string
  hostname: string
  url: string // what a caller hits, ready to paste into a client
  created_at: string
}

export interface AccessPathList {
  hostname: string
  paths: AccessPath[]
}

// null means the console's own hostname, which is not a project.
const accessPathsUrl = (projectId: string | null) =>
  projectId ? `/api/projects/${projectId}/access/paths` : '/api/access/paths'

export const fetchAccessPaths = (projectId: string | null) =>
  getJson<AccessPathList>(accessPathsUrl(projectId))

export const addAccessPath = (projectId: string | null, path: string) =>
  request<AccessPath>(accessPathsUrl(projectId), jsonInit('POST', { path }))

export const removeAccessPath = (projectId: string | null, pathId: string) =>
  request<void>(`${accessPathsUrl(projectId)}/${pathId}`, jsonInit('DELETE'))

export interface SettingsStatus {
  set: string[]
}

export const fetchSettings = () => getJson<SettingsStatus>('/api/settings')

export const sendTestAlert = () =>
  request<{ sent: boolean }>('/api/alerts/test', jsonInit('POST'))

export interface CredentialStatus {
  key: string
  label: string
  set: boolean
  expires_at: string | null
  days_left: number | null
  state: string // ok | warn | expired | none
}

export const fetchCredentials = () => getJson<CredentialStatus[]>('/api/credentials')

export const setCredentialExpiry = (key: string, expires_at: string | null) =>
  request<void>(`/api/credentials/${key}/expiry`, jsonInit('PUT', { expires_at }))

export type TokenScope = 'read' | 'write'

export interface ApiToken {
  id: string
  name: string
  preview: string
  scope: TokenScope
  created_at: string
  last_used_at: string | null
}

/** Only the create response ever carries the token itself. */
export interface CreatedApiToken extends ApiToken {
  token: string
}

export const fetchApiTokens = () => getJson<ApiToken[]>('/api/tokens')

export const createApiToken = (name: string, scope: TokenScope) =>
  request<CreatedApiToken>('/api/tokens', jsonInit('POST', { name, scope }))

export const revokeApiToken = (id: string) =>
  request<void>(`/api/tokens/${id}`, jsonInit('DELETE'))

export const fetchBackups = () => getJson<BackupStatus>('/api/backups')

export const runBackupNow = () =>
  request<{ run_id: string; status: string }>('/api/backups', jsonInit('POST'))

export const putSetting = (key: string, value: string) =>
  request<void>(`/api/settings/${key}`, jsonInit('PUT', { value }))

export const deleteSetting = (key: string) =>
  request<void>(`/api/settings/${key}`, jsonInit('DELETE'))

export const fetchSecrets = (projectId: string) =>
  getJson<SecretMeta[]>(`/api/projects/${projectId}/secrets`)

export const putSecret = (projectId: string, key: string, value: string) =>
  request<void>(`/api/projects/${projectId}/secrets/${key}`, jsonInit('PUT', { value }))

export const revealSecret = (projectId: string, key: string) =>
  request<{ key: string; value: string }>(
    `/api/projects/${projectId}/secrets/${key}/reveal`,
    jsonInit('POST'),
  )

export const deleteSecret = (projectId: string, key: string) =>
  request<void>(`/api/projects/${projectId}/secrets/${key}`, jsonInit('DELETE'))

export interface ImportResult {
  added: string[]
  updated: string[]
  skipped: string[]
}

export interface TomlValidation {
  valid: boolean
  error: string | null
  warnings: string[]
  summary: {
    name: string
    subdomain: string
    port: number
    health: string
    resources: string
    env_keys: string[]
    secrets: string[]
  } | null
}

export interface StarterFiles {
  console_toml: string
  dockerfile: string
  workflow: string
}

export const importSecrets = (projectId: string, text: string) =>
  request<ImportResult>(
    `/api/projects/${projectId}/secrets/import`,
    jsonInit('POST', { text }),
  )

export const exportSecrets = (projectId: string) =>
  request<{ env: string }>(
    `/api/projects/${projectId}/secrets/export`,
    jsonInit('POST'),
  )

export const validateConsoleToml = (text: string) =>
  request<TomlValidation>('/api/validate/console-toml', jsonInit('POST', { text }))

export const fetchStarters = (projectId: string) =>
  getJson<StarterFiles>(`/api/projects/${projectId}/starters`)

export const fetchDeployments = (projectId: string) =>
  getJson<DeploymentSummary[]>(`/api/projects/${projectId}/deployments`)

export const fetchDeployment = (projectId: string, deploymentId: string) =>
  getJson<DeploymentDetail>(
    `/api/projects/${projectId}/deployments/${deploymentId}`,
  )

export const rollbackDeployment = (projectId: string, deploymentId: string) =>
  request<{ deployment_id: string; status: string }>(
    `/api/projects/${projectId}/deployments/${deploymentId}/rollback`,
    jsonInit('POST'),
  )

export const redeployDeployment = (projectId: string, deploymentId: string) =>
  request<{ deployment_id: string; status: string }>(
    `/api/projects/${projectId}/deployments/${deploymentId}/redeploy`,
    jsonInit('POST'),
  )

// Build the repo at a ref on the server and deploy what comes out. The
// console does this on its own for a push to the tracked branch; this is the
// button for a specific ref, or a retry.
export const requestBuild = (projectId: string, ref?: string) =>
  request<{ deployment_id: string; status: string }>(
    `/api/projects/${projectId}/builds`,
    jsonInit('POST', { ref }),
  )

// Deploy an image that is already in GHCR, with no build webhook involved.
// console.toml is read from the repo at `ref` unless one is pasted, which is
// the fallback for when GitHub cannot be reached.
export const deployImage = (
  projectId: string,
  body: { image: string; ref?: string; console_toml?: string },
) =>
  request<{ deployment_id: string; status: string }>(
    `/api/projects/${projectId}/deployments`,
    jsonInit('POST', body),
  )

export interface GitHubStatus {
  app_configured: boolean // client id and secret are both set, so connecting is possible
  connected: boolean // a token is stored
  login: string | null // the connected account, null if the token no longer works
  error: string | null
}

export interface GitHubRepo {
  full_name: string
  default_branch: string
  private: boolean
  pushed_at: string | null // the list is ordered by this, newest first
}

export const fetchGitHubStatus = () => getJson<GitHubStatus>('/api/github/status')

// A full page navigation, not fetch: the browser has to follow the redirect to
// github.com and come back to the callback carrying the state cookie.
export const GITHUB_AUTHORIZE_PATH = '/api/github/authorize'

export const disconnectGitHub = () =>
  request<void>('/api/github/connection', jsonInit('DELETE'))

export const fetchGitHubRepos = () =>
  getJson<{ repos: GitHubRepo[] }>('/api/github/repos')

export const fetchGitHubBranches = (repo: string) =>
  getJson<{ branches: string[] }>(
    `/api/github/branches?repo=${encodeURIComponent(repo)}`,
  )

export interface ProjectContainer {
  state: string // running | exited | created | absent | …
  name?: string
  image?: string
  started_at?: string | null
  finished_at?: string | null
  exit_code?: number | null
}

export type ControlAction = 'start' | 'stop' | 'restart'

export const fetchProjectContainer = (projectId: string) =>
  getJson<ProjectContainer>(`/api/projects/${projectId}/container`)

export const controlApp = (projectId: string, action: ControlAction) =>
  request<ProjectContainer>(
    `/api/projects/${projectId}/controls/${action}`,
    jsonInit('POST'),
  )

export const runCommand = (projectId: string, command: string) =>
  request<{ run_id: string; status: string }>(
    `/api/projects/${projectId}/commands`,
    jsonInit('POST', { command }),
  )

export const fetchCommandRuns = (projectId: string) =>
  getJson<CommandRunSummary[]>(`/api/projects/${projectId}/commands`)

export const fetchCommandRun = (projectId: string, runId: string) =>
  getJson<CommandRunDetail>(`/api/projects/${projectId}/commands/${runId}`)
