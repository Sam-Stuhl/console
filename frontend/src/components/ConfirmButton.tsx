import { useEffect, useRef, useState } from 'react'

/* Two-step inline confirm: first click arms it, second click within 3s
   fires. No modal. */
export default function ConfirmButton({
  label,
  confirmLabel,
  onConfirm,
}: {
  label: string
  confirmLabel: string
  onConfirm: () => void
}) {
  const [armed, setArmed] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  function click() {
    if (armed) {
      setArmed(false)
      onConfirm()
      return
    }
    setArmed(true)
    timer.current = window.setTimeout(() => setArmed(false), 3000)
  }

  return (
    <button
      type="button"
      onClick={click}
      className={`btn btn-sm font-mono ${armed ? 'btn-error' : 'btn-outline btn-error'}`}
    >
      {armed ? confirmLabel : label}
    </button>
  )
}
