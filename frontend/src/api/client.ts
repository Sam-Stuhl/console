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

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json() as Promise<T>
}

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
