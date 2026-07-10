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
}

export interface SecretMeta {
  key: string
  updated_at: string
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
}) => request<Project>('/api/projects', jsonInit('POST', body))

export const deleteProject = (id: string) =>
  request<void>(`/api/projects/${id}`, jsonInit('DELETE'))

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
