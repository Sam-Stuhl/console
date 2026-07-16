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
}

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
    throw new Error(detail)
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
