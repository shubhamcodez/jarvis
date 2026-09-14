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
  const cap = liveUsage?.max_tokens || 80000
  const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0
  const openTasks = (tasks || []).filter((t) =>
    ['pending', 'active', 'blocked', 'waiting_approval', 'paused', 'error'].includes(t.status),
  )
  const liveRuns = runs || []

  return (
    <div className="control-plane" role="region" aria-label="Run control">
      <div className="control-plane__row">
        <div className="control-plane__modes" role="group" aria-label="Run mode">
          {['plan', 'draft', 'agent'].map((m) => (
            <button
              key={m}
              type="button"
              className={`control-plane__mode${runMode === m ? ' control-plane__mode--on' : ''}`}
              onClick={() => onRunMode?.(m)}
              aria-pressed={runMode === m}
            >
              {m === 'plan' ? 'Plan' : m === 'draft' ? 'Draft' : 'Agent'}
            </button>
          ))}
        </div>
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
