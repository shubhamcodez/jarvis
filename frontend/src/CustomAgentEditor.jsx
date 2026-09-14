import { useEffect, useMemo, useState } from 'react'
import {
  deleteCustomAgent,
  deleteCustomAgentKnowledge,
  deleteCustomAgentRoutine,
  deleteCustomAgentSkill,
  duplicateCustomAgent,
  getCustomAgent,
  getCustomAgentToolCatalog,
  patchCustomAgent,
  saveCustomAgentMemory,
  saveCustomAgentRoutine,
  saveCustomAgentSkill,
  testCustomAgentRoutine,
  uploadCustomAgentKnowledge,
} from './api'

const TABS = [
  { id: 'profile', label: 'Profile' },
  { id: 'memory', label: 'Memory' },
  { id: 'skills', label: 'Skills' },
  { id: 'tools', label: 'Tools' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'schedule', label: 'Schedule' },
]

const EMPTY_SKILL = {
  name: '',
  description: '',
  body: `# Instructions\n\n1. When to use this skill\n2. Required inputs\n3. Sequence of work\n4. How to validate the result\n5. What to return\n6. What requires approval\n`,
}

function localTz() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local'
  } catch {
    return 'local'
  }
}

function formatBytes(n) {
  const x = Number(n) || 0
  if (x < 1024) return `${x} B`
  if (x < 1024 * 1024) return `${Math.round(x / 1024)} KB`
  return `${(x / (1024 * 1024)).toFixed(1)} MB`
}

function formatWhen(iso) {
  if (!iso) return '—'
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function CustomAgentEditor({ agentId, onClose, onChanged, onDeleted }) {
  const [tab, setTab] = useState('profile')
  const [agent, setAgent] = useState(null)
  const [toolsCatalog, setToolsCatalog] = useState({ tools: [], groups: [] })
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [flash, setFlash] = useState('')
  const [skillDraft, setSkillDraft] = useState(EMPTY_SKILL)
  const [editingSkillId, setEditingSkillId] = useState(null)
  const [routineDraft, setRoutineDraft] = useState({
    name: 'Daily run',
    prompt: '',
    enabled: true,
    skill_id: '',
    kind: 'daily',
    time: '08:00',
    timezone: localTz(),
    weekdays: [0, 1, 2, 3, 4],
    at: '',
    interval_minutes: 60,
    run_mode: 'isolated',
    respect_quiet_hours: true,
  })
  const [editingRoutineId, setEditingRoutineId] = useState(null)

  const load = async () => {
    if (!agentId) return
    setError('')
    try {
      const data = await getCustomAgent(agentId)
      setAgent(data)
    } catch (e) {
      setError(e?.message || 'Could not load agent.')
    }
  }

  useEffect(() => {
    load()
    getCustomAgentToolCatalog()
      .then(setToolsCatalog)
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId])

  const ping = (msg) => {
    setFlash(msg)
    window.setTimeout(() => setFlash(''), 2200)
    onChanged?.()
  }

  const saveProfile = async (patch) => {
    setBusy(true)
    setError('')
    try {
      const data = await patchCustomAgent(agentId, patch)
      setAgent(data)
      ping('Saved')
    } catch (e) {
      setError(e?.message || 'Save failed.')
    }
    setBusy(false)
  }

  const knownToolNames = useMemo(() => {
    const fromGroups = (toolsCatalog.groups || []).flatMap((g) => g.tools || [])
    const fromList = (toolsCatalog.tools || []).map((t) => t.name)
    return [...new Set([...fromGroups, ...fromList])]
  }, [toolsCatalog])

  if (!agent) {
    return (
      <aside className="agent-drawer" aria-label="Agent settings">
        <div className="agent-drawer__head">
          <h2>Agent</h2>
          <button type="button" className="agent-drawer__close" onClick={onClose}>
            Close
          </button>
        </div>
        <p className="agent-drawer__status">{error || 'Loading…'}</p>
      </aside>
    )
  }

  return (
    <aside className="agent-drawer" aria-label={`${agent.name} settings`}>
      <div className="agent-drawer__head">
        <div>
          <h2>
            <span aria-hidden>{agent.emoji || '✦'}</span> {agent.name}
          </h2>
          <p className="agent-drawer__sub">{agent.title || 'Custom agent'}</p>
        </div>
        <button type="button" className="agent-drawer__close" onClick={onClose}>
          Close
        </button>
      </div>
      <div className="agent-drawer__tabs" role="tablist">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={tab === t.id}
            className={`agent-drawer__tab${tab === t.id ? ' agent-drawer__tab--active' : ''}`}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {error ? <p className="agent-drawer__error">{error}</p> : null}
      {flash ? <p className="agent-drawer__flash">{flash}</p> : null}

      <div className="agent-drawer__body">
        {tab === 'profile' ? (
          <div className="agent-form">
            <label>
              Emoji
              <input
                value={agent.emoji || ''}
                maxLength={8}
                onChange={(e) => setAgent({ ...agent, emoji: e.target.value })}
                onBlur={() => saveProfile({ emoji: agent.emoji })}
              />
            </label>
            <label>
              Name
              <input
                value={agent.name || ''}
                onChange={(e) => setAgent({ ...agent, name: e.target.value })}
                onBlur={() => saveProfile({ name: agent.name })}
              />
            </label>
            <label>
              Title
              <input
                value={agent.title || ''}
                placeholder="e.g. Expense manager"
                onChange={(e) => setAgent({ ...agent, title: e.target.value })}
                onBlur={() => saveProfile({ title: agent.title })}
              />
            </label>
            <label>
              Job description
              <textarea
                rows={6}
                value={agent.description || ''}
                placeholder="One job, working style, and approval boundary."
                onChange={(e) => setAgent({ ...agent, description: e.target.value })}
                onBlur={() => saveProfile({ description: agent.description })}
              />
            </label>
            <label>
              Approval boundary
              <textarea
                rows={3}
                value={agent.approval_boundary || ''}
                onChange={(e) => setAgent({ ...agent, approval_boundary: e.target.value })}
                onBlur={() => saveProfile({ approval_boundary: agent.approval_boundary })}
              />
            </label>
            <div className="agent-form__checks">
              <label className="agent-check">
                <input
                  type="checkbox"
                  checked={!!agent.pinned}
                  onChange={(e) => saveProfile({ pinned: e.target.checked })}
                />
                Pin in sidebar
              </label>
              <label className="agent-check">
                <input
                  type="checkbox"
                  checked={!!agent.hidden}
                  onChange={(e) => saveProfile({ hidden: e.target.checked })}
                />
                Hide from sidebar
              </label>
              <label className="agent-check">
                <input
                  type="checkbox"
                  checked={!!agent.memory_enabled}
                  onChange={(e) => saveProfile({ memory_enabled: e.target.checked })}
                />
                Enable memory
              </label>
            </div>
            <div className="agent-form__actions">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  try {
                    const copy = await duplicateCustomAgent(agentId)
                    ping(`Duplicated as ${copy.name}`)
                    onChanged?.(copy)
                  } catch (e) {
                    setError(e?.message || 'Duplicate failed.')
                  }
                  setBusy(false)
                }}
              >
                Duplicate
              </button>
              <button
                type="button"
                className="agent-btn-danger"
                disabled={busy}
                onClick={async () => {
                  if (!confirm(`Delete ${agent.name}? This removes its chat, skills, knowledge, and routines.`)) return
                  setBusy(true)
                  try {
                    await deleteCustomAgent(agentId)
                    onDeleted?.(agentId)
                  } catch (e) {
                    setError(e?.message || 'Delete failed.')
                    setBusy(false)
                  }
                }}
              >
                Delete agent
              </button>
            </div>
          </div>
        ) : null}

        {tab === 'memory' ? (
          <div className="agent-form">
            <p className="agent-help">
              Curated long-term memory (MEMORY.md). Chat <code>remember …</code> appends a note. Do not store
              changing source-of-truth data here.
            </p>
            <textarea
              className="agent-form__code"
              rows={18}
              value={agent.memory || ''}
              onChange={(e) => setAgent({ ...agent, memory: e.target.value })}
            />
            <button
              type="button"
              disabled={busy}
              onClick={async () => {
                setBusy(true)
                try {
                  const data = await saveCustomAgentMemory(agentId, agent.memory || '')
                  setAgent({ ...agent, memory: data.content })
                  ping('Memory saved')
                } catch (e) {
                  setError(e?.message || 'Could not save memory.')
                }
                setBusy(false)
              }}
            >
              Save memory
            </button>
          </div>
        ) : null}

        {tab === 'skills' ? (
          <div className="agent-form">
            <p className="agent-help">
              Each skill is a <code>SKILL.md</code> with a description (when to use it) and step-by-step
              instructions. Matching skills are loaded into context for a turn.
            </p>
            <ul className="agent-file-list">
              {(agent.skills || []).map((s) => (
                <li key={s.id}>
                  <button
                    type="button"
                    className="agent-file-list__open"
                    onClick={() => {
                      setEditingSkillId(s.id)
                      setSkillDraft({
                        name: s.name,
                        description: s.description || '',
                        body: (s.content || '').replace(/^---[\s\S]*?---\s*/, ''),
                      })
                    }}
                  >
                    <strong>{s.name}</strong>
                    <span>{s.description}</span>
                  </button>
                  {s.id !== 'role-playbook' ? (
                    <button
                      type="button"
                      className="agent-btn-ghost"
                      onClick={async () => {
                        if (!confirm(`Delete skill ${s.name}?`)) return
                        await deleteCustomAgentSkill(agentId, s.id)
                        await load()
                        ping('Skill deleted')
                      }}
                    >
                      ×
                    </button>
                  ) : null}
                </li>
              ))}
            </ul>
            <h3 className="agent-h3">{editingSkillId ? 'Edit skill' : 'New skill'}</h3>
            <label>
              Name (slug)
              <input
                value={skillDraft.name}
                placeholder="weekly-account-health"
                onChange={(e) => setSkillDraft({ ...skillDraft, name: e.target.value })}
              />
            </label>
            <label>
              Description (what + when)
              <textarea
                rows={3}
                value={skillDraft.description}
                onChange={(e) => setSkillDraft({ ...skillDraft, description: e.target.value })}
              />
            </label>
            <label>
              SKILL.md body
              <textarea
                className="agent-form__code"
                rows={12}
                value={skillDraft.body}
                onChange={(e) => setSkillDraft({ ...skillDraft, body: e.target.value })}
              />
            </label>
            <div className="agent-form__actions">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  setError('')
                  try {
                    await saveCustomAgentSkill(agentId, {
                      name: skillDraft.name,
                      description: skillDraft.description,
                      body: skillDraft.body,
                      skillId: editingSkillId,
                    })
                    setSkillDraft(EMPTY_SKILL)
                    setEditingSkillId(null)
                    await load()
                    ping('Skill saved')
                  } catch (e) {
                    setError(e?.message || 'Could not save skill.')
                  }
                  setBusy(false)
                }}
              >
                Save skill
              </button>
              {editingSkillId ? (
                <button
                  type="button"
                  className="agent-btn-ghost"
                  onClick={() => {
                    setEditingSkillId(null)
                    setSkillDraft(EMPTY_SKILL)
                  }}
                >
                  Cancel edit
                </button>
              ) : null}
            </div>
          </div>
        ) : null}

        {tab === 'tools' ? (
          <div className="agent-form">
            <p className="agent-help">
              Only enabled tools run for this agent. Keep send/delete/desktop/shell off unless the job needs them.
            </p>
            {(toolsCatalog.groups || []).map((g) => (
              <fieldset key={g.id} className="agent-tools-group">
                <legend>{g.label}</legend>
                {(g.tools || []).map((name) => {
                  const info = (toolsCatalog.tools || []).find((t) => t.name === name)
                  const checked = (agent.tools || []).includes(name)
                  return (
                    <label key={name} className="agent-check">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          const next = new Set(agent.tools || [])
                          if (e.target.checked) next.add(name)
                          else next.delete(name)
                          const tools = [...next]
                          setAgent({ ...agent, tools })
                          saveProfile({ tools })
                        }}
                      />
                      <span>
                        <strong>{name}</strong>
                        {info?.description ? ` — ${info.description}` : ''}
                      </span>
                    </label>
                  )
                })}
              </fieldset>
            ))}
            {knownToolNames.length === 0 ? <p className="agent-help">Tool catalog unavailable.</p> : null}
          </div>
        ) : null}

        {tab === 'knowledge' ? (
          <div className="agent-form">
            <p className="agent-help">
              Upload reference files. Small text files are included in context; larger ones are searched by keyword.
            </p>
            <input
              type="file"
              multiple
              onChange={async (e) => {
                const files = Array.from(e.target.files || [])
                e.target.value = ''
                if (!files.length) return
                setBusy(true)
                try {
                  const data = await uploadCustomAgentKnowledge(agentId, files)
                  setAgent({ ...agent, knowledge: data.files })
                  ping(`Uploaded ${data.added?.length || files.length} file(s)`)
                } catch (err) {
                  setError(err?.message || 'Upload failed.')
                }
                setBusy(false)
              }}
            />
            <ul className="agent-file-list">
              {(agent.knowledge || []).length === 0 ? (
                <li className="agent-help">No files yet.</li>
              ) : (
                (agent.knowledge || []).map((f) => (
                  <li key={f.name}>
                    <span>
                      <strong>{f.name}</strong>
                      <span>
                        {' '}
                        {formatBytes(f.size)} {f.indexed ? '· indexed' : '· not text-indexed'}
                      </span>
                    </span>
                    <button
                      type="button"
                      className="agent-btn-ghost"
                      onClick={async () => {
                        await deleteCustomAgentKnowledge(agentId, f.name)
                        await load()
                      }}
                    >
                      ×
                    </button>
                  </li>
                ))
              )}
            </ul>
          </div>
        ) : null}

        {tab === 'schedule' ? (
          <div className="agent-form">
            <p className="agent-help">
              A routine tells this agent <em>when</em> to run a prompt (and optional skill). The backend must be
              running. Unattended runs draft in this conversation; they will not send mail or drive the desktop.
            </p>
            <ul className="agent-file-list">
              {(agent.routines || []).map((r) => (
                <li key={r.id} className="agent-routine">
                  <div>
                    <strong>{r.name}</strong>
                    <span>
                      {' '}
                      {r.enabled ? 'on' : 'paused'} · next {formatWhen(r.next_run_at)} · last {r.last_status || '—'}
                    </span>
                    <p className="agent-help">{r.prompt}</p>
                    {(r.runs || []).slice(0, 3).map((run, i) => (
                      <p key={i} className="agent-help">
                        {formatWhen(run.at)} · {run.status}
                        {run.error ? ` — ${run.error}` : ''}
                      </p>
                    ))}
                  </div>
                  <div className="agent-routine__btns">
                    <button
                      type="button"
                      className="agent-btn-ghost"
                      onClick={() => {
                        const sch = r.schedule || {}
                        setEditingRoutineId(r.id)
                        setRoutineDraft({
                          name: r.name,
                          prompt: r.prompt,
                          enabled: !!r.enabled,
                          skill_id: r.skill_id || '',
                          kind: sch.kind || 'daily',
                          time: sch.time || '08:00',
                          timezone: sch.timezone || localTz(),
                          weekdays: sch.weekdays || [0, 1, 2, 3, 4],
                          at: sch.at || '',
                          interval_minutes: sch.interval_minutes || 60,
                          run_mode: r.run_mode || 'isolated',
                          respect_quiet_hours: r.respect_quiet_hours !== false,
                        })
                      }}
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      className="agent-btn-ghost"
                      disabled={busy}
                      onClick={async () => {
                        setBusy(true)
                        try {
                          const data = await testCustomAgentRoutine(agentId, r.id)
                          ping('Test run finished — see the chat')
                          onChanged?.({ ...agent, chat_id: data.chat_id })
                          await load()
                        } catch (e) {
                          setError(e?.message || 'Test run failed.')
                        }
                        setBusy(false)
                      }}
                    >
                      Test
                    </button>
                    <button
                      type="button"
                      className="agent-btn-ghost"
                      onClick={async () => {
                        if (!confirm('Delete this routine?')) return
                        await deleteCustomAgentRoutine(agentId, r.id)
                        await load()
                      }}
                    >
                      ×
                    </button>
                  </div>
                </li>
              ))}
            </ul>
            <h3 className="agent-h3">{editingRoutineId ? 'Edit routine' : 'New routine'}</h3>
            <label>
              Name
              <input
                value={routineDraft.name}
                onChange={(e) => setRoutineDraft({ ...routineDraft, name: e.target.value })}
              />
            </label>
            <label>
              Prompt
              <textarea
                rows={4}
                value={routineDraft.prompt}
                placeholder="Every weekday, run the daily briefing skill and post a watch list here."
                onChange={(e) => setRoutineDraft({ ...routineDraft, prompt: e.target.value })}
              />
            </label>
            <label>
              Skill (optional)
              <select
                value={routineDraft.skill_id}
                onChange={(e) => setRoutineDraft({ ...routineDraft, skill_id: e.target.value })}
              >
                <option value="">None — use matching skills</option>
                {(agent.skills || []).map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              When
              <select
                value={routineDraft.kind}
                onChange={(e) => setRoutineDraft({ ...routineDraft, kind: e.target.value })}
              >
                <option value="daily">Daily / weekdays at a time</option>
                <option value="once">Once at a date and time</option>
                <option value="interval">Every N minutes</option>
              </select>
            </label>
            {routineDraft.kind === 'interval' ? (
              <label>
                Minutes
                <input
                  type="number"
                  min={5}
                  max={1440}
                  value={routineDraft.interval_minutes}
                  onChange={(e) =>
                    setRoutineDraft({ ...routineDraft, interval_minutes: Number(e.target.value) || 60 })
                  }
                />
              </label>
            ) : null}
            {routineDraft.kind === 'once' ? (
              <label>
                Run at
                <input
                  type="datetime-local"
                  value={routineDraft.at}
                  onChange={(e) => setRoutineDraft({ ...routineDraft, at: e.target.value })}
                />
              </label>
            ) : null}
            {routineDraft.kind === 'daily' ? (
              <>
                <label>
                  Time
                  <input
                    type="time"
                    value={routineDraft.time}
                    onChange={(e) => setRoutineDraft({ ...routineDraft, time: e.target.value })}
                  />
                </label>
                <div className="agent-weekdays">
                  {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((label, i) => (
                    <label key={label} className="agent-check">
                      <input
                        type="checkbox"
                        checked={(routineDraft.weekdays || []).includes(i)}
                        onChange={(e) => {
                          const set = new Set(routineDraft.weekdays || [])
                          if (e.target.checked) set.add(i)
                          else set.delete(i)
                          setRoutineDraft({ ...routineDraft, weekdays: [...set].sort() })
                        }}
                      />
                      {label}
                    </label>
                  ))}
                </div>
              </>
            ) : null}
            <label>
              Time zone
              <input
                value={routineDraft.timezone}
                onChange={(e) => setRoutineDraft({ ...routineDraft, timezone: e.target.value })}
              />
            </label>
            <label className="agent-check">
              <input
                type="checkbox"
                checked={!!routineDraft.enabled}
                onChange={(e) => setRoutineDraft({ ...routineDraft, enabled: e.target.checked })}
              />
              Enabled
            </label>
            <label>
              Run mode
              <select
                value={routineDraft.run_mode || 'isolated'}
                onChange={(e) => setRoutineDraft({ ...routineDraft, run_mode: e.target.value })}
              >
                <option value="isolated">Isolated (agent chat)</option>
                <option value="continue">Continue (current Ada chat)</option>
              </select>
            </label>
            <label className="agent-check">
              <input
                type="checkbox"
                checked={routineDraft.respect_quiet_hours !== false}
                onChange={(e) => setRoutineDraft({ ...routineDraft, respect_quiet_hours: e.target.checked })}
              />
              Respect quiet hours
            </label>
            <div className="agent-form__actions">
              <button
                type="button"
                disabled={busy}
                onClick={async () => {
                  setBusy(true)
                  setError('')
                  try {
                    const atIso =
                      routineDraft.kind === 'once' && routineDraft.at
                        ? new Date(routineDraft.at).toISOString()
                        : routineDraft.at
                    await saveCustomAgentRoutine(
                      agentId,
                      {
                        name: routineDraft.name,
                        prompt: routineDraft.prompt,
                        enabled: routineDraft.enabled,
                        skill_id: routineDraft.skill_id || null,
                        run_mode: routineDraft.run_mode || 'isolated',
                        respect_quiet_hours: routineDraft.respect_quiet_hours !== false,
                        schedule: {
                          kind: routineDraft.kind,
                          time: routineDraft.time,
                          timezone: routineDraft.timezone,
                          weekdays: routineDraft.weekdays,
                          at: atIso,
                          interval_minutes: routineDraft.interval_minutes,
                        },
                      },
                      editingRoutineId,
                    )
                    setEditingRoutineId(null)
                    await load()
                    ping('Routine saved')
                  } catch (e) {
                    setError(e?.message || 'Could not save routine.')
                  }
                  setBusy(false)
                }}
              >
                Save routine
              </button>
              {editingRoutineId ? (
                <button
                  type="button"
                  className="agent-btn-ghost"
                  onClick={() => setEditingRoutineId(null)}
                >
                  Cancel edit
                </button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>
    </aside>
  )
}
