export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KiB', 'MiB', 'GiB', 'TiB']
  let value = n
  let i = -1
  do {
    value /= 1024
    i++
  } while (value >= 1024 && i < units.length - 1)
  return `${value.toFixed(value >= 100 ? 0 : 1)} ${units[i]}`
}

export function duration(fromIso: string | null): string {
  if (!fromIso) return ''
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(fromIso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ${minutes % 60}m`
  const days = Math.floor(hours / 24)
  return `${days}d ${hours % 24}h`
}

export function localTime(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString()
}

// API timestamps from SQLite are naive UTC (no Z suffix); treat them as UTC.
export function utcDate(iso: string): Date {
  return new Date(/[zZ]|[+-]\d\d:\d\d$/.test(iso) ? iso : iso + 'Z')
}

export function localDay(iso: string): string {
  return utcDate(iso).toLocaleDateString()
}

export function since(iso: string | null): string {
  if (!iso) return ''
  const seconds = Math.max(0, Math.floor((Date.now() - utcDate(iso).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export function took(fromIso: string | null, toIso: string | null): string {
  if (!fromIso || !toIso) return ''
  const seconds = Math.round((utcDate(toIso).getTime() - utcDate(fromIso).getTime()) / 1000)
  if (seconds < 0) return ''
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}
