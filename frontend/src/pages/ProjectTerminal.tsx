import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import '@xterm/xterm/css/xterm.css'

type Status = 'connecting' | 'open' | 'closed'

export default function ProjectTerminal() {
  const { id } = useParams<{ id: string }>()
  const host = useRef<HTMLDivElement>(null)
  const [status, setStatus] = useState<Status>('connecting')

  useEffect(() => {
    if (!id || !host.current) return

    const term = new Terminal({
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, "Cascadia Code", monospace',
      fontSize: 13,
      theme: { background: '#0a0a0a' },
      cursorBlink: true,
    })
    const fit = new FitAddon()
    term.loadAddon(fit)
    term.open(host.current)
    fit.fit()

    const encoder = new TextEncoder()
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    const ws = new WebSocket(
      `${proto}://${window.location.host}/api/projects/${id}/terminal`,
    )
    ws.binaryType = 'arraybuffer'

    const sendResize = () => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ resize: { cols: term.cols, rows: term.rows } }))
      }
    }

    ws.onopen = () => {
      setStatus('open')
      sendResize()
      term.focus()
    }
    ws.onmessage = (e) => {
      if (typeof e.data === 'string') term.write(e.data)
      else term.write(new Uint8Array(e.data))
    }
    ws.onclose = () => {
      setStatus('closed')
      term.write('\r\n\x1b[90m[session closed]\x1b[0m\r\n')
    }

    // Keystrokes go as binary; resize control goes as text JSON.
    const onData = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(encoder.encode(data))
    })

    const onResize = () => {
      fit.fit()
      sendResize()
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      onData.dispose()
      ws.close()
      term.dispose()
    }
  }, [id])

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-4">
        <Link
          to={`/projects/${id}`}
          className="font-mono text-xs text-muted hover:text-base-content"
        >
          &larr; project
        </Link>
        <h1 className="font-mono text-xl font-semibold">terminal</h1>
        <span className="inline-flex items-center gap-1.5 font-mono text-xs text-muted">
          <span
            className={`size-1.5 rounded-full ${
              status === 'open'
                ? 'bg-success'
                : status === 'connecting'
                  ? 'bg-warning motion-safe:animate-pulse'
                  : 'bg-base-300'
            }`}
          />
          {status}
        </span>
      </div>
      <p className="font-mono text-xs text-faint">
        an interactive shell inside the live container. changes to the container
        filesystem are lost on the next deploy.
      </p>
      <div
        ref={host}
        className="h-[70vh] overflow-hidden rounded-box border border-base-300/60 bg-neutral p-2"
      />
    </div>
  )
}
