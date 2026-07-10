import { useState } from 'react'

/* A textarea that also accepts a dropped text file. Used for .env import
   and the console.toml checker. */
export default function DropTextArea({
  value,
  onChange,
  placeholder,
  rows = 8,
}: {
  value: string
  onChange: (text: string) => void
  placeholder: string
  rows?: number
}) {
  const [dragging, setDragging] = useState(false)

  async function onDrop(e: React.DragEvent) {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) onChange(await file.text())
  }

  return (
    <textarea
      value={value}
      onChange={(e) => onChange(e.target.value)}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      placeholder={placeholder}
      rows={rows}
      spellCheck={false}
      className={`textarea w-full border bg-base-100 font-mono text-xs leading-relaxed transition-colors duration-150 ${
        dragging ? 'border-primary' : 'border-base-300'
      }`}
    />
  )
}
