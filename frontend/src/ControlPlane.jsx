import { useEffect, useRef, useState } from 'react'

const ICON_AGENT = (
  <svg className="mode-menu__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
    <path d="M8 8a4 4 0 1 1 0 8H5a3 3 0 0 1 0-6h10a3 3 0 1 0 0-6H8" strokeLinecap="round" />
  </svg>
)

const ICON_PLAN = (
  <svg className="mode-menu__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
    <path d="M8 6h12M8 12h12M8 18h12" strokeLinecap="round" />
    <circle cx="4" cy="6" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="4" cy="12" r="1.2" fill="currentColor" stroke="none" />
    <circle cx="4" cy="18" r="1.2" fill="currentColor" stroke="none" />
  </svg>
)

const ICON_DRAFT = (
  <svg className="mode-menu__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden>
    <path d="M14 4H7a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" />
    <path d="M14 4v5h5" />
  </svg>
)

const ICON_CHECK = (
  <svg className="mode-menu__check" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden>
    <path d="M5 12.5l4.2 4.2L19 7.5" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

const ICON_CHEVRON = (
  <svg className="mode-menu__chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <path d="M6 9l6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
)

const MODES = [
  { id: 'agent', label: 'Agent', hint: 'Uses Settings → Autonomy', icon: ICON_AGENT },
  { id: 'plan', label: 'Plan', hint: 'Blocks shell, file writes, email, and GUI', icon: ICON_PLAN },
  { id: 'draft', label: 'Draft', hint: 'Asks before those actions', icon: ICON_DRAFT },
]

export function ModeMenu({ runMode, onRunMode }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)
  const current = MODES.find((m) => m.id === runMode) || MODES[0]

  useEffect(() => {
    if (!open) return undefined
    const onDoc = (e) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div className="mode-menu" ref={wrapRef}>
      <button
        type="button"
        className="mode-menu__trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={`Run mode: ${current.label}. ${current.hint}`}
        title={`${current.label}: ${current.hint}`}
        onClick={() => setOpen((v) => !v)}
      >
        {current.icon}
        <span>{current.label}</span>
        {ICON_CHEVRON}
      </button>
      {open ? (
        <div className="mode-menu__pop" role="listbox" aria-label="Run mode">
          {MODES.map((m) => {
            const on = m.id === current.id
            return (
              <button
                key={m.id}
                type="button"
                role="option"
                aria-selected={on}
                className={`mode-menu__item${on ? ' mode-menu__item--on' : ''}`}
                onClick={() => {
                  onRunMode?.(m.id)
                  setOpen(false)
                }}
              >
                {m.icon}
                <span className="mode-menu__item-text">
                  <span className="mode-menu__item-label">{m.label}</span>
                  <span className="mode-menu__hint">{m.hint}</span>
                </span>
                {on ? ICON_CHECK : null}
              </button>
            )
          })}
        </div>
      ) : null}
    </div>
  )
}

export function ControlPlane({
  runMode,
  onRunMode,
  liveUsage,
  tasks,
  runs,
  onResumeTask,
  onCancelTask,
  onStopRun,
  onSteer,
  sending,
}) {
  const used = liveUsage?.tokens_used || 0
  const cap = liveUsage?.max_tokens ?? 80000
  const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0
  const openTasks = (tasks || []).filter((t) =>
    ['pending', 'active', 'blocked', 'waiting_approval', 'paused', 'error'].includes(t.status),
  )
  const liveRuns = runs || []
  const showStrip = used > 0 || liveRuns.length > 0 || openTasks.length > 0

  if (!showStrip) return null

  return (
    <div className="control-plane" role="region" aria-label="Run control">
      <div className="control-plane__row">
        <div className="control-plane__meter" title={`${used} / ${cap} tokens this run`}>
          <span className="control-plane__meter-label">
            {used} / {cap} tok
          </span>
          <span className="control-plane__meter-bar" aria-hidden="true">
            <span style={{ width: `${pct}%` }} />
          </span>
        </div>
      </div>
      {liveRuns.length > 0 ? (
        <ul className="control-plane__runs">
          {liveRuns.map((r) => (
            <li key={r.run_id} className="control-plane__run">
              <span>
                {r.status}
                {(r.children || [])
                  .filter((c) => c.status === 'running')
                  .map((c) => ` · ${c.agent}`)
                  .join('')}
              </span>
              <button type="button" className="control-plane__mini" onClick={() => onStopRun?.(r.run_id)}>
                Stop
              </button>
              {sending ? (
                <button
                  type="button"
                  className="control-plane__mini"
                  onClick={() => {
                    const note = window.prompt('Steer this run (one instruction):')
                    if (note) onSteer?.(r.run_id, note)
                  }}
                >
                  Steer
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {openTasks.length > 0 ? (
        <ul className="control-plane__tasks">
          {openTasks.slice(0, 4).map((t) => (
            <li key={t.id} className="control-plane__task">
              <div>
                <strong>{t.status}</strong> {t.title || t.goal}
                {t.next_action ? <em> · next: {t.next_action}</em> : null}
              </div>
              <div className="control-plane__task-btns">
                {t.resumable && ['paused', 'error', 'blocked', 'waiting_approval'].includes(t.status) ? (
                  <button type="button" className="control-plane__mini" onClick={() => onResumeTask?.(t)}>
                    Resume
                  </button>
                ) : null}
                <button type="button" className="control-plane__mini" onClick={() => onCancelTask?.(t.id)}>
                  Cancel
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
