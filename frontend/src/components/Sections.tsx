/**
 * Reusable collapsible sections + a Notion-style table of contents.
 *
 * Wrap a page in <SectionsProvider>, use <CollapsibleSection id title> for each
 * block, and drop a <TableOfContents /> anywhere (a sticky aside works well).
 * The TOC lists every section in document order, scroll-spies the one in view,
 * and clicking an entry expands its section and scrolls to it.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react'

interface Entry {
  id: string
  title: string
}

interface SectionsContext {
  entries: Entry[]
  openMap: Record<string, boolean>
  register: (id: string, title: string, defaultOpen: boolean) => void
  unregister: (id: string) => void
  setOpen: (id: string, open: boolean) => void
  toggle: (id: string) => void
  setAll: (open: boolean) => void
  activeId: string | null
  setActiveId: (id: string | null) => void
}

const Ctx = createContext<SectionsContext | null>(null)

function useSections(): SectionsContext {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('use CollapsibleSection/TableOfContents within <SectionsProvider>')
  return ctx
}

export function SectionsProvider({
  children,
  storageKey,
}: {
  children: ReactNode
  // When set, the open/closed state of each section is remembered under this
  // key in localStorage, so it survives reloads and navigation.
  storageKey?: string
}) {
  const [entries, setEntries] = useState<Entry[]>([])
  const [openMap, setOpenMap] = useState<Record<string, boolean>>(() => {
    if (!storageKey) return {}
    try {
      const raw = localStorage.getItem(storageKey)
      return raw ? (JSON.parse(raw) as Record<string, boolean>) : {}
    } catch {
      return {}
    }
  })
  const [activeId, setActiveId] = useState<string | null>(null)

  useEffect(() => {
    if (!storageKey) return
    try {
      localStorage.setItem(storageKey, JSON.stringify(openMap))
    } catch {
      // storage unavailable (private mode, quota); state just won't persist
    }
  }, [storageKey, openMap])

  const register = useCallback((id: string, title: string, defaultOpen: boolean) => {
    setEntries((prev) => (prev.some((e) => e.id === id) ? prev : [...prev, { id, title }]))
    setOpenMap((prev) => (id in prev ? prev : { ...prev, [id]: defaultOpen }))
  }, [])
  const unregister = useCallback((id: string) => {
    setEntries((prev) => prev.filter((e) => e.id !== id))
  }, [])
  const setOpen = useCallback(
    (id: string, open: boolean) => setOpenMap((prev) => ({ ...prev, [id]: open })),
    [],
  )
  const toggle = useCallback(
    (id: string) => setOpenMap((prev) => ({ ...prev, [id]: !prev[id] })),
    [],
  )
  const setAll = useCallback((open: boolean) => {
    // Every mounted section has its id in openMap (register seeds it), so
    // flipping the existing keys covers them all.
    setOpenMap((prev) => Object.fromEntries(Object.keys(prev).map((id) => [id, open])))
  }, [])

  return (
    <Ctx.Provider
      value={{
        entries,
        openMap,
        register,
        unregister,
        setOpen,
        toggle,
        setAll,
        activeId,
        setActiveId,
      }}
    >
      {children}
    </Ctx.Provider>
  )
}

/**
 * A single toggle that expands every section when any is collapsed, and
 * collapses them all otherwise. Render it anywhere inside <SectionsProvider>.
 */
export function ExpandCollapseAll({ className = '' }: { className?: string }) {
  const { entries, openMap, setAll } = useSections()
  if (entries.length === 0) return null
  const anyOpen = entries.some((e) => openMap[e.id])
  return (
    <button
      type="button"
      onClick={() => setAll(!anyOpen)}
      className={`font-mono text-[11px] text-muted transition-colors duration-150 hover:text-base-content ${className}`}
    >
      {anyOpen ? 'collapse all' : 'expand all'}
    </button>
  )
}

export function CollapsibleSection({
  id,
  title,
  defaultOpen = true,
  children,
}: {
  id: string
  title: string
  defaultOpen?: boolean
  children: ReactNode
}) {
  const { register, unregister, openMap, toggle } = useSections()

  useEffect(() => {
    register(id, title, defaultOpen)
    return () => unregister(id)
  }, [id, title, defaultOpen, register, unregister])

  const open = openMap[id] ?? defaultOpen

  return (
    <section id={id} className="scroll-mt-20 border-t border-base-300 pt-4">
      <button
        type="button"
        onClick={() => toggle(id)}
        aria-expanded={open}
        className="group flex w-full items-center gap-2"
      >
        <Chevron open={open} />
        <span className="font-mono text-xs text-muted transition-colors duration-150 group-hover:text-base-content">
          {title}
        </span>
      </button>
      {/* grid-rows 0fr -> 1fr animates height without measuring */}
      <div
        className={`grid transition-[grid-template-rows] duration-150 ${
          open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]'
        }`}
      >
        <div className="overflow-hidden">
          <div className="flex flex-col gap-3 pt-3">{children}</div>
        </div>
      </div>
    </section>
  )
}

export function TableOfContents() {
  const { entries, activeId, setActiveId, setOpen } = useSections()

  useEffect(() => {
    if (entries.length === 0) return
    const observer = new IntersectionObserver(
      (records) => {
        const top = records
          .filter((r) => r.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
        if (top) setActiveId(top.target.id)
      },
      // Trip the active section a bit before it reaches the very top.
      { rootMargin: '-10% 0px -70% 0px', threshold: 0 },
    )
    entries.forEach((e) => {
      const el = document.getElementById(e.id)
      if (el) observer.observe(el)
    })
    return () => observer.disconnect()
  }, [entries, setActiveId])

  function jump(id: string) {
    setOpen(id, true)
    setActiveId(id)
    // Let the section expand before scrolling to its settled position.
    window.setTimeout(
      () => document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' }),
      170,
    )
  }

  if (entries.length === 0) return null

  return (
    <nav className="flex flex-col gap-1 font-mono text-xs">
      {entries.map((e) => (
        <button
          key={e.id}
          type="button"
          onClick={() => jump(e.id)}
          className={`truncate border-l-2 py-0.5 pl-2 text-left transition-colors duration-150 ${
            activeId === e.id
              ? 'border-primary text-base-content'
              : 'border-transparent text-muted hover:text-base-content'
          }`}
        >
          {e.title}
        </button>
      ))}
    </nav>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      width="10"
      height="10"
      aria-hidden
      className={`text-muted transition-transform duration-150 ${open ? 'rotate-90' : ''}`}
    >
      <path
        d="M4 2l4 4-4 4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
