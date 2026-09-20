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
      const files = session.files
      const loaded = new Array(files.length)
      let next = 0
      const worker = async () => {
        while (true) {
          const i = next
          next += 1
          if (i >= files.length || cancelled) return
          const item = files[i]
          const path = item.path
          const proposed = item.content ?? ''
          let base = ''
          try {
            base = (await resolveBaseContent(path)) || ''
          } catch {
            base = ''
          }
          if (cancelled) return
          loaded[i] = { path, base, proposed }
        }
      }
      const n = Math.min(6, files.length)
      await Promise.all(Array.from({ length: n }, () => worker()))
      if (cancelled) return
      setFilesState(loaded)
    })()
    return () => {
      cancelled = true
    }
  }, [session?.id, resolveBaseContent])

  return filesState
}

const PATCH_CACHE_MAX = 48
const patchCache = new Map()

function patchCacheKey(base, proposed) {
  const b = base ?? ''
  const p = proposed ?? ''
  return `${b.length}:${p.length}:${b.slice(0, 32)}:${p.slice(0, 32)}`
}

/** Cached structuredPatch (context: 2) keyed by lengths + first 32 chars. */
export function getOrBuildPatch(base, proposed) {
  const key = patchCacheKey(base, proposed)
  if (patchCache.has(key)) return patchCache.get(key)
  const patch = structuredPatch('a', 'b', base ?? '', proposed ?? '', 'a', 'b', { context: 2 })
  if (patchCache.has(key)) patchCache.delete(key)
  patchCache.set(key, patch)
  while (patchCache.size > PATCH_CACHE_MAX) {
    patchCache.delete(patchCache.keys().next().value)
  }
  return patch
}

/** Count added / removed lines for a mini summary (+n -m). */
export function countPatchLines(base, proposed) {
  const patch = getOrBuildPatch(base, proposed)
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
