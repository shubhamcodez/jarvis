import { useEffect, useState } from 'react'
import { structuredPatch } from 'diff'

/**
 * Load base + proposed pairs for a workspace edit session (same logic as review panel).
 */
export function useLoadedWorkspaceFiles(session, resolveBaseContent) {
  const [filesState, setFilesState] = useState([])

  useEffect(() => {
    if (!session?.files?.length) {
      setFilesState([])
      return
    }
    let cancelled = false
    ;(async () => {
      const loaded = []
      for (const item of session.files) {
        const path = item.path
        const proposed = item.content ?? ''
        let base = ''
        try {
          base = (await resolveBaseContent(path)) || ''
        } catch {
          base = ''
        }
        if (cancelled) return
        loaded.push({ path, base, proposed })
      }
      if (cancelled) return
      setFilesState(loaded)
    })()
    return () => {
      cancelled = true
    }
  }, [session?.id, resolveBaseContent])

  return filesState
}

/** Count added / removed lines for a mini summary (+n -m). */
export function countPatchLines(base, proposed) {
  const patch = structuredPatch('a', 'b', base ?? '', proposed ?? '', 'a', 'b', { context: 2 })
  let add = 0
  let del = 0
  for (const h of patch.hunks || []) {
    for (const line of h.lines || []) {
      const c = line[0]
      if (c === '+') add += 1
      else if (c === '-') del += 1
    }
  }
  return { add, del }
}
