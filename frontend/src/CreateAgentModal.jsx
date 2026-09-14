import { useEffect, useRef, useState } from 'react'

export function CreateAgentModal({ open, busy, error, onClose, onSubmit }) {
  const [brief, setBrief] = useState('')
  const inputRef = useRef(null)

  useEffect(() => {
    if (!open) return undefined
    setBrief('')
    const t = window.setTimeout(() => inputRef.current?.focus(), 40)
    const onKey = (e) => {
      if (e.key === 'Escape' && !busy) onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.clearTimeout(t)
      window.removeEventListener('keydown', onKey)
    }
  }, [open, busy, onClose])

  if (!open) return null

  const submit = (e) => {
    e?.preventDefault?.()
    const text = brief.trim()
    if (!text || busy) return
    onSubmit(text)
  }

  return (
    <div
      className="agent-modal-overlay"
      role="presentation"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onClose()
      }}
    >
      <div
        className="agent-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-agent-title"
      >
        <h2 id="create-agent-title">New agent</h2>
        <p className="agent-modal__lead">
          Describe the job in operational terms — what it owns, what it should produce, and what it must never do
          without your approval.
        </p>
        <form onSubmit={submit}>
          <textarea
            ref={inputRef}
            rows={7}
            value={brief}
            disabled={busy}
            placeholder="Example: Own the weekly account-health review. Pull usage and support signals, flag churn risk, and post a watch list here. Never contact a customer without approval."
            onChange={(e) => setBrief(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                e.preventDefault()
                submit(e)
              }
            }}
          />
          {error ? <p className="agent-modal__error">{error}</p> : null}
          <div className="agent-modal__actions">
            <button type="button" className="agent-modal__ghost" onClick={onClose} disabled={busy}>
              Cancel
            </button>
            <button type="submit" disabled={busy || !brief.trim()}>
              {busy ? 'Creating…' : 'Create agent'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
