import { useState } from 'react'
import { projectIconUrl, type Project } from '../api/client'

function initials(name: string): string {
  const joined = name
    .split(/[-_ ]/)
    .map((word) => word[0])
    .join('')
  return (joined.slice(0, 2) || name.slice(0, 2)).toUpperCase()
}

/**
 * A project's icon: the app's own fetched favicon when there is one, else the
 * initials tile. Falls back to initials if the image fails to load.
 */
export default function ProjectIcon({
  project,
  size = 20,
  rounded = 'rounded-sm',
}: {
  project: Project
  size?: number
  rounded?: string
}) {
  const [failed, setFailed] = useState(false)
  const box = `flex flex-none items-center justify-center overflow-hidden ${rounded}`
  const dims = { width: size, height: size }

  if (project.has_icon && !failed) {
    return (
      <span className={`${box} bg-base-100`} style={dims}>
        <img
          src={projectIconUrl(project)}
          alt=""
          className="h-full w-full object-contain"
          onError={() => setFailed(true)}
        />
      </span>
    )
  }
  return (
    <span
      className={`${box} bg-primary font-mono font-bold text-primary-content`}
      style={{ ...dims, fontSize: Math.round(size * 0.42) }}
    >
      {initials(project.name)}
    </span>
  )
}
