import { useCallback, useEffect, useMemo, useState } from 'react'
import { applyPatch, structuredPatch } from 'diff'
import { useLoadedWorkspaceFiles } from './workspaceReviewData'

/**
 * Apply only accepted hunks from a structured patch. Returns null if patch application fails.
 */
export function mergeWithAcceptedHunks(base, proposed, acceptedHunkIndices) {
  const patch = structuredPatch('a', 'b', base ?? '', proposed ?? '', 'a', 'b', { context: 2 })
  const all = patch.hunks || []
  if (all.length === 0) return (proposed ?? '') === (base ?? '') ? base : proposed
  const set = new Set(acceptedHunkIndices)
  const picked = all.filter((_, i) => set.has(i))
  if (picked.length === 0) return base ?? ''
  if (picked.length === all.length) return proposed ?? ''
  try {
    const filtered = { ...patch, hunks: picked }
    const out = applyPatch(base ?? '', filtered)
    return typeof out === 'string' ? out : null
  } catch {
    return null
  }
}

export function WorkspaceFileReview({
  session,
  workspaceLabel,
  resolveBaseContent,
  onWriteFile,
  onDismiss,
  onOpenFile,
  variant = 'default',
  activeFileIndex: controlledFileIndex,
  onActiveFileIndexChange,
  onFileRemovedFromQueue,
}) {
  const loadedFiles = useLoadedWorkspaceFiles(session, resolveBaseContent)
  const [removedPaths, setRemovedPaths] = useState(() => new Set())
  const filesState = useMemo(
    () => loadedFiles.filter((f) => !removedPaths.has(f.path)),
    [loadedFiles, removedPaths],
  )

  const [internalFileIndex, setInternalFileIndex] = useState(0)
  const [acceptedByPath, setAcceptedByPath] = useState(() => new Map())
  const [applyError, setApplyError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [focusHunkIndex, setFocusHunkIndex] = useState(0)

  const isWorkbench = variant === 'workbench'

  const activeIndex = controlledFileIndex !== undefined ? controlledFileIndex : internalFileIndex

  const setFileIndex = useCallback(
    (next) => {
      const n = typeof next === 'function' ? next(activeIndex) : next
      const clamped = Math.max(0, Math.min(n, Math.max(0, filesState.length - 1)))
      if (onActiveFileIndexChange) onActiveFileIndexChange(clamped)
      else setInternalFileIndex(clamped)
    },
    [activeIndex, filesState.length, onActiveFileIndexChange],
  )

  useEffect(() => {
    setInternalFileIndex(0)
    setRemovedPaths(new Set())
  }, [session?.id])

  useEffect(() => {
    if (filesState.length === 0) return
    setAcceptedByPath((prev) => {
      const next = new Map()
      for (const { path, base, proposed } of filesState) {
        if (prev.has(path)) {
          next.set(path, prev.get(path))
          continue
        }
        const patch = structuredPatch(path, path, base, proposed, path, path, { context: 2 })
        const n = patch.hunks?.length ?? 0
        next.set(path, n ? new Set(Array.from({ length: n }, (_, i) => i)) : new Set())
      }
      return next
    })
  }, [filesState])

  useEffect(() => {
    if (filesState.length === 0) return
    if (controlledFileIndex !== undefined && controlledFileIndex >= filesState.length) {
      onActiveFileIndexChange?.(Math.max(0, filesState.length - 1))
    }
  }, [filesState.length, controlledFileIndex, onActiveFileIndexChange])

  const active = filesState[activeIndex] || null

  const patch = useMemo(() => {
    if (!active) return null
    return structuredPatch(
      active.path,
      active.path,
      active.base,
      active.proposed,
      active.path,
      active.path,
      { context: 2 },
    )
  }, [active])

  const hunks = patch?.hunks || []
  const acceptedSet = active ? acceptedByPath.get(active.path) || new Set() : new Set()

  useEffect(() => {
    setFocusHunkIndex(0)
  }, [activeIndex, filesState])

  const focusHunk = Math.min(focusHunkIndex, Math.max(0, hunks.length - 1))

  useEffect(() => {
    if (!isWorkbench || hunks.length === 0) return
    const el = document.getElementById(`workbench-hunk-${focusHunk}`)
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [isWorkbench, focusHunk, hunks.length, active?.path])

  const toggleHunk = useCallback(
    (hunkIdx) => {
      if (!active) return
      setAcceptedByPath((prev) => {
        const m = new Map(prev)
        const s = new Set(m.get(active.path) || [])
        if (s.has(hunkIdx)) s.delete(hunkIdx)
        else s.add(hunkIdx)
        m.set(active.path, s)
        return m
      })
    },
    [active],
  )

  const keepAllHunksForActive = useCallback(() => {
    if (!active || !hunks.length) return
    setAcceptedByPath((prev) => {
      const m = new Map(prev)
      m.set(active.path, new Set(Array.from({ length: hunks.length }, (_, i) => i)))
      return m
    })
  }, [active, hunks.length])

  const rejectAllHunksForActive = useCallback(() => {
    if (!active) return
    setAcceptedByPath((prev) => {
      const m = new Map(prev)
      m.set(active.path, new Set())
      return m
    })
  }, [active])

  const onKeepFile = useCallback(async () => {
    if (!active || busy) return
    setBusy(true)
    setApplyError(null)
    try {
      const r = await onWriteFile(active.path, active.proposed)
      if (!r?.ok) {
        setApplyError(r?.error || 'Could not write file.')
        return
      }
      const ap = active.path
      const nextCount = filesState.filter((f) => f.path !== ap).length
      onFileRemovedFromQueue?.(ap)
      setRemovedPaths((prev) => new Set(prev).add(ap))
      setAcceptedByPath((prev) => {
        const m = new Map(prev)
        m.delete(ap)
        return m
      })
      if (nextCount === 0) onDismiss()
      else setFileIndex((i) => Math.min(i, nextCount - 1))
    } finally {
      setBusy(false)
    }
  }, [active, busy, onWriteFile, filesState, setFileIndex, onDismiss, onFileRemovedFromQueue])

  const onDiscardFile = useCallback(() => {
    if (!active || busy) return
    const ap = active.path
    const nextCount = filesState.filter((f) => f.path !== ap).length
    onFileRemovedFromQueue?.(ap)
    setRemovedPaths((prev) => new Set(prev).add(ap))
    setAcceptedByPath((prev) => {
      const m = new Map(prev)
      m.delete(ap)
      return m
    })
    if (nextCount === 0) onDismiss()
    else setFileIndex((i) => Math.min(i, nextCount - 1))
  }, [active, busy, filesState, setFileIndex, onDismiss, onFileRemovedFromQueue])

  const onApplyMerged = useCallback(async () => {
    if (!active || busy) return
    const idxArr = [...acceptedSet].sort((a, b) => a - b)
    const merged = mergeWithAcceptedHunks(active.base, active.proposed, idxArr)
    if (merged == null) {
      setApplyError('Could not apply selected hunks (conflict). Try Keep entire file or edit manually.')
      return
    }
    setBusy(true)
    setApplyError(null)
    try {
      const r = await onWriteFile(active.path, merged)
      if (!r?.ok) {
        setApplyError(r?.error || 'Could not write file.')
        return
      }
      const ap = active.path
      const nextCount = filesState.filter((f) => f.path !== ap).length
      onFileRemovedFromQueue?.(ap)
      setRemovedPaths((prev) => new Set(prev).add(ap))
      setAcceptedByPath((prev) => {
        const m = new Map(prev)
        m.delete(ap)
        return m
      })
      if (nextCount === 0) onDismiss()
      else setFileIndex((i) => Math.min(i, nextCount - 1))
    } finally {
      setBusy(false)
    }
  }, [active, acceptedSet, busy, onWriteFile, filesState, setFileIndex, onDismiss, onFileRemovedFromQueue])

  useEffect(() => {
    const onKey = (e) => {
      if (!session?.files?.length) return
      if (e.target.closest?.('textarea, input:not([type="checkbox"]):not([type="radio"]), [contenteditable="true"]')) {
        return
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        e.preventDefault()
        if (!busy) void onKeepFile()
      }
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        if (!busy) void onKeepFile()
      }
      if (isWorkbench && hunks.length > 0 && (e.ctrlKey || e.metaKey)) {
        if (e.key === 'n' && !e.shiftKey) {
          e.preventDefault()
          toggleHunk(focusHunk)
        }
        if (e.shiftKey && (e.key === 'y' || e.key === 'Y')) {
          e.preventDefault()
          if (!acceptedSet.has(focusHunk)) toggleHunk(focusHunk)
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [session?.files?.length, busy, onKeepFile, isWorkbench, hunks.length, focusHunk, acceptedSet, toggleHunk])

  if (!session?.files?.length) return null
  if (filesState.length === 0) {
    return (
      <div
        className={`workspace-file-review workspace-file-review--loading${isWorkbench ? ' workspace-file-review--workbench' : ''}`}
        role="status"
      >
        Loading diff…
      </div>
    )
  }

  const fileIdxLabel = `${activeIndex + 1} of ${filesState.length}`
  const title = workspaceLabel ? `${workspaceLabel} — review changes` : 'Review changes'

  const hunkNav =
    hunks.length > 0 ? (
      <div className="workspace-file-review__hunk-float">
        <button
          type="button"
          className="workspace-file-review__icon-btn"
          disabled={focusHunk <= 0}
          onClick={() => setFocusHunkIndex((i) => Math.max(0, i - 1))}
          aria-label="Previous change"
        >
          ‹
        </button>
        <span className="workspace-file-review__hunk-float-label">
          Change {focusHunk + 1} of {hunks.length}
        </span>
        <button
          type="button"
          className="workspace-file-review__icon-btn"
          disabled={focusHunk >= hunks.length - 1}
          onClick={() => setFocusHunkIndex((i) => Math.min(hunks.length - 1, i + 1))}
          aria-label="Next change"
        >
          ›
        </button>
        <button
          type="button"
          className="workspace-file-review__btn workspace-file-review__btn--small workspace-file-review__btn--ghost"
          onClick={() => toggleHunk(focusHunk)}
        >
          {acceptedSet.has(focusHunk) ? 'Undo hunk' : 'Keep hunk'}
        </button>
        <span className="workspace-file-review__hunk-float-hint" title="Shortcuts">
          Ctrl+⌘N toggle · Ctrl+⇧Y keep
        </span>
      </div>
    ) : null

  return (
    <div
      className={`workspace-file-review${isWorkbench ? ' workspace-file-review--workbench' : ''}`}
      role="region"
      aria-label="Workspace file changes"
    >
      <div className="workspace-file-review__header">
        {!isWorkbench ? <span className="workspace-file-review__title">{title}</span> : null}
        {isWorkbench ? (
          <span className="workspace-file-review__title workspace-file-review__title--path" title={active?.path}>
            {active?.path || '—'}
          </span>
        ) : null}
        <div className="workspace-file-review__nav">
          <button
            type="button"
            className="workspace-file-review__icon-btn"
            disabled={activeIndex <= 0}
            onClick={() => setFileIndex((i) => Math.max(0, i - 1))}
            aria-label="Previous file"
          >
            ‹
          </button>
          <span className="workspace-file-review__counter">{fileIdxLabel}</span>
          <button
            type="button"
            className="workspace-file-review__icon-btn"
            disabled={activeIndex >= filesState.length - 1}
            onClick={() => setFileIndex((i) => Math.min(filesState.length - 1, i + 1))}
            aria-label="Next file"
          >
            ›
          </button>
        </div>
        <div className="workspace-file-review__file-actions">
          <button
            type="button"
            className="workspace-file-review__btn workspace-file-review__btn--ghost"
            disabled={busy}
            onClick={onDiscardFile}
          >
            Undo file
          </button>
          <button
            type="button"
            className="workspace-file-review__btn workspace-file-review__btn--primary"
            disabled={busy}
            onClick={onKeepFile}
            title="Accept all changes in this file (Ctrl+Enter)"
          >
            Keep file (Ctrl+S)
          </button>
        </div>
        <button type="button" className="workspace-file-review__close" onClick={onDismiss} aria-label="Dismiss review">
          ×
        </button>
      </div>

      {isWorkbench ? (
        <p className="workspace-file-review__workbench-hint">
          Main editor: red removes &middot; green adds. Use the right panel to jump between files.
        </p>
      ) : null}

      {applyError ? <div className="workspace-file-review__error">{applyError}</div> : null}

      {active ? (
        <>
          {!isWorkbench ? (
            <div className="workspace-file-review__path-row">
              <button
                type="button"
                className="workspace-file-review__path-link"
                onClick={() => onOpenFile?.(active.path)}
                title="Open in file preview"
              >
                {active.path}
              </button>
              <div className="workspace-file-review__bulk">
                <button
                  type="button"
                  className="workspace-file-review__link-btn"
                  onClick={keepAllHunksForActive}
                  disabled={!hunks.length}
                >
                  Select all hunks
                </button>
                <button
                  type="button"
                  className="workspace-file-review__link-btn"
                  onClick={rejectAllHunksForActive}
                  disabled={!hunks.length}
                >
                  Reject all hunks
                </button>
                <button
                  type="button"
                  className="workspace-file-review__btn workspace-file-review__btn--accent"
                  disabled={
                    busy || !hunks.length || acceptedSet.size === hunks.length || acceptedSet.size === 0
                  }
                  onClick={onApplyMerged}
                  title="Write merged result using only kept hunks"
                >
                  Apply merged
                </button>
              </div>
            </div>
          ) : (
            <div className="workspace-file-review__path-row workspace-file-review__path-row--compact">
              <div className="workspace-file-review__bulk">
                <button
                  type="button"
                  className="workspace-file-review__link-btn"
                  onClick={keepAllHunksForActive}
                  disabled={!hunks.length}
                >
                  Select all hunks
                </button>
                <button
                  type="button"
                  className="workspace-file-review__link-btn"
                  onClick={rejectAllHunksForActive}
                  disabled={!hunks.length}
                >
                  Reject all hunks
                </button>
                <button
                  type="button"
                  className="workspace-file-review__btn workspace-file-review__btn--accent workspace-file-review__btn--small"
                  disabled={
                    busy || !hunks.length || acceptedSet.size === hunks.length || acceptedSet.size === 0
                  }
                  onClick={onApplyMerged}
                >
                  Apply merged
                </button>
                <button
                  type="button"
                  className="workspace-file-review__link-btn"
                  onClick={() => onOpenFile?.(active.path)}
                >
                  Open in preview tab
                </button>
              </div>
            </div>
          )}

          <div
            className={`workspace-file-review__diff-wrap${isWorkbench ? ' workspace-file-review__diff-wrap--unified' : ''}`}
          >
            {hunks.length === 0 ? (
              <p className="workspace-file-review__same">No line differences (or empty file).</p>
            ) : (
              hunks.map((hunk, hi) => {
                const kept = acceptedSet.has(hi)
                const isFocus = isWorkbench && hi === focusHunk
                return (
                  <div
                    key={`${active.path}-${hi}`}
                    id={isWorkbench ? `workbench-hunk-${hi}` : undefined}
                    className={`workspace-file-review__hunk${kept ? '' : ' workspace-file-review__hunk--rejected'}${isFocus ? ' workspace-file-review__hunk--focus' : ''}`}
                  >
                    {!isWorkbench ? (
                      <div className="workspace-file-review__hunk-toolbar">
                        <span className="workspace-file-review__hunk-meta">
                          Change {hi + 1} of {hunks.length}
                        </span>
                        <div className="workspace-file-review__hunk-actions">
                          <button
                            type="button"
                            className={
                              kept
                                ? 'workspace-file-review__btn workspace-file-review__btn--small'
                                : 'workspace-file-review__btn workspace-file-review__btn--small workspace-file-review__btn--ghost'
                            }
                            onClick={() => toggleHunk(hi)}
                          >
                            {kept ? 'Undo' : 'Keep'}
                          </button>
                        </div>
                      </div>
                    ) : null}
                    <pre className="workspace-file-review__hunk-pre">
                      {(hunk.lines || []).map((line, li) => {
                        const c = line[0]
                        const rest = line.slice(1)
                        let cls = 'workspace-file-review__ln'
                        if (c === '+') cls += ' workspace-file-review__ln--add'
                        else if (c === '-') cls += ' workspace-file-review__ln--del'
                        else cls += ' workspace-file-review__ln--ctx'
                        return (
                          <div key={li} className={cls}>
                            <span className="workspace-file-review__ln-mark">{c}</span>
                            {rest}
                          </div>
                        )
                      })}
                    </pre>
                  </div>
                )
              })
            )}
          </div>
          {isWorkbench ? hunkNav : null}
        </>
      ) : null}
    </div>
  )
}
