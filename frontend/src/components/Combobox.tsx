import { useEffect, useId, useMemo, useRef, useState } from 'react'

/**
 * A filterable picker for lists that are too long to scan and too short to
 * paginate: repos, branches. A native <select> cannot filter, shows nothing
 * about an option beyond its label, and is unusable at fifty items, which is
 * why every tool that picks a repo (GitHub's branch switcher, Vercel's import)
 * uses this shape instead.
 *
 * Typing filters. Arrows move, enter takes, escape backs out. The list carries
 * the detail that actually decides the choice, so the pick can be made without
 * opening another tab to check.
 */

export interface ComboboxItem {
  value: string
  /** The machine name. Monospace, and what filtering matches against. */
  label: string
  /** A short qualifier: "private", "default". Never more than one word. */
  tag?: string
  /** Right-aligned detail, e.g. when the repo was last pushed to. */
  detail?: string
}

export default function Combobox({
  label,
  value,
  items,
  onSelect,
  placeholder,
  hint,
  emptyText = 'nothing matches',
  loading = false,
  required = false,
}: {
  label: string
  value: string
  items: ComboboxItem[]
  onSelect: (value: string) => void
  placeholder: string
  hint: React.ReactNode
  emptyText?: string
  loading?: boolean
  required?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const wrapper = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLInputElement>(null)
  const listId = useId()

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return items
    return items.filter((i) => i.label.toLowerCase().includes(q))
  }, [items, query])

  // Keep the highlight on a row that still exists as the filter narrows.
  useEffect(() => {
    setActive((current) => (current < matches.length ? current : 0))
  }, [matches.length])

  // Close on a click elsewhere. pointerdown rather than click, so the list is
  // gone before whatever was clicked reacts.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!wrapper.current?.contains(event.target as Node)) close()
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  function close() {
    setOpen(false)
    setQuery('')
  }

  function choose(item: ComboboxItem) {
    onSelect(item.value)
    close()
    input.current?.blur()
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === 'Escape') {
      close()
      return
    }
    if (event.key === 'Enter' && open) {
      event.preventDefault()
      const item = matches[active]
      if (item) choose(item)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) {
        setOpen(true)
        return
      }
      const step = event.key === 'ArrowDown' ? 1 : -1
      setActive((current) => {
        if (matches.length === 0) return 0
        return (current + step + matches.length) % matches.length
      })
    }
  }

  return (
    <div className="flex flex-col gap-1" ref={wrapper}>
      <label htmlFor={`${listId}-input`} className="font-mono text-xs text-muted">
        {label}
      </label>

      <div className="relative">
        <input
          id={`${listId}-input`}
          ref={input}
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={open && matches[active] ? `${listId}-${active}` : undefined}
          autoComplete="off"
          spellCheck={false}
          required={required && !value}
          // Showing the selection while closed and the query while open keeps
          // one field doing both jobs, so nothing shifts as it opens.
          value={open ? query : value}
          placeholder={value ? value : placeholder}
          onChange={(e) => {
            setQuery(e.target.value)
            setActive(0)
            if (!open) setOpen(true)
          }}
          onFocus={() => setOpen(true)}
          // Focus alone is not enough: after escape the field keeps focus, so
          // no focus event fires and clicking it again would do nothing.
          onClick={() => setOpen(true)}
          onKeyDown={onKeyDown}
          className="input input-sm w-full border-base-300 bg-base-100 pr-8 font-mono text-sm"
        />

        <Chevron open={open} />

        {open && (
          // The only floating layer in the app, hence the lone z-index.
          <div
            // The theme runs depth 0 everywhere else, but this is the one
            // element that genuinely floats over other controls, and those
            // controls share its surface color. Without a shadow its bottom
            // edge reads as another row rather than the end of the list.
            className="absolute top-full right-0 left-0 z-20 mt-1 overflow-hidden rounded-box border border-base-300 bg-base-100 shadow-[0_6px_20px_rgba(0,0,0,0.55)]"
            // Opens fast enough not to be waited on. Reduced motion gets the
            // end state with no travel; see index.css.
            style={{ animation: 'combobox-open 120ms cubic-bezier(0.16, 1, 0.3, 1)' }}
          >
            <ul id={listId} role="listbox" className="max-h-64 overflow-y-auto">
              {loading &&
                [0, 1, 2].map((i) => (
                  <li key={i} className="border-b border-base-300/40 px-3 py-2 last:border-none">
                    <span className="skeleton block h-3 w-40" />
                  </li>
                ))}

              {!loading && matches.length === 0 && (
                <li className="px-3 py-2 font-mono text-xs text-faint">{emptyText}</li>
              )}

              {!loading &&
                matches.map((item, i) => {
                  const selected = item.value === value
                  return (
                    <li key={item.value} id={`${listId}-${i}`} role="option" aria-selected={selected}>
                      <button
                        type="button"
                        // pointerdown would fire before the input's blur, but
                        // mousedown-prevent keeps focus where it is instead.
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => choose(item)}
                        onMouseEnter={() => setActive(i)}
                        className={`flex w-full items-baseline gap-3 border-b border-base-300/40 px-3 py-2 text-left transition-colors duration-150 last:border-none ${
                          i === active ? 'bg-base-300/40' : ''
                        }`}
                      >
                        <span
                          aria-hidden
                          className={`font-mono text-xs ${
                            selected ? 'text-primary' : 'text-transparent'
                          }`}
                        >
                          &#10003;
                        </span>
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-base-content">
                          {item.label}
                        </span>
                        {item.tag && (
                          <span className="font-mono text-[11px] whitespace-nowrap text-muted">
                            {item.tag}
                          </span>
                        )}
                        {item.detail && (
                          // muted, not faint: this is what explains the
                          // ordering, so it has to be readable at 11px.
                          <span className="font-mono text-[11px] whitespace-nowrap text-muted">
                            {item.detail}
                          </span>
                        )}
                      </button>
                    </li>
                  )
                })}
            </ul>
          </div>
        )}
      </div>

      <span className="font-mono text-xs text-faint">{hint}</span>
    </div>
  )
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      width="10"
      height="10"
      aria-hidden
      className={`pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-muted transition-transform duration-150 ${
        open ? 'rotate-180' : ''
      }`}
    >
      <path
        d="M2 4.5l4 4 4-4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
