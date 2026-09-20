import { useMemo } from 'react'
import { countPatchLines, getOrBuildPatch, useLoadedWorkspaceFiles } from './workspaceReviewData'


function miniDiffPreview(base, proposed, maxLines = 6) {
  const patch = getOrBuildPatch(base, proposed)
  const lines = []
  for (const h of patch.hunks || []) {
    for (const line of h.lines || []) {
      if (lines.length >= maxLines) return lines
      const c = line[0]
      if (c === ' ') continue
      lines.push({ c, t: line.slice(1) })
    }
  }
  return lines
}

export function CodingEditSummaryCards({ session, resolveBaseContent, activePath, onSelectPath }) {
  const filesState = useLoadedWorkspaceFiles(session, resolveBaseContent)

  const rows = useMemo(() => {
    return filesState.map((f) => ({
      path: f.path,
      ...countPatchLines(f.base, f.proposed),
      mini: miniDiffPreview(f.base, f.proposed),
    }))
  }, [filesState])

  if (!session?.files?.length) return null

  return (
    <div className="coding-edit-cards" aria-label="Proposed file changes">
      {rows.length === 0 ? (
        <div className="coding-edit-cards__loading">Loading change summary…</div>
      ) : (
        rows.map((row) => (
          <button
            key={row.path}
            type="button"
            className={`coding-edit-card${row.path === activePath ? ' coding-edit-card--active' : ''}`}
            onClick={() => onSelectPath?.(row.path)}
          >
            <div className="coding-edit-card__head">
              <span className="coding-edit-card__path" title={row.path}>
                {row.path}
              </span>
              <span className="coding-edit-card__stats">
                {row.add > 0 ? <span className="coding-edit-card__add">+{row.add}</span> : null}
                {row.del > 0 ? <span className="coding-edit-card__del">−{row.del}</span> : null}
                {row.add === 0 && row.del === 0 ? <span className="coding-edit-card__same">0</span> : null}
              </span>
            </div>
            {row.mini.length > 0 ? (
              <pre className="coding-edit-card__mini">
                {row.mini.map((l, i) => (
                  <div
                    key={i}
                    className={
                      l.c === '+'
                        ? 'coding-edit-card__ln coding-edit-card__ln--add'
                        : 'coding-edit-card__ln coding-edit-card__ln--del'
                    }
                  >
                    {l.t.length > 120 ? `${l.t.slice(0, 117)}…` : l.t}
                  </div>
                ))}
              </pre>
            ) : null}
          </button>
        ))
      )}
    </div>
  )
}
