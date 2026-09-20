/**
 * @fileoverview @-mentions for project file paths in the chat input.
 */

/**
 * If the cursor is inside an active @file token, return its span. The token starts at @
 * (only after start-of-string or whitespace) and ends before the next whitespace.
 * @param {string} text
 * @param {number} cursorPos
 * @returns {{ start: number, query: string } | null}
 */
export function getActiveFileMention(text, cursorPos) {
  const src = text == null ? '' : String(text)
  const pos = Math.max(0, Math.min(cursorPos ?? 0, src.length))
  const before = src.slice(0, pos)
  const at = before.lastIndexOf('@')
  if (at === -1) return null
  if (at > 0) {
    const prev = before[at - 1]
    if (prev !== undefined && !/\s/.test(prev)) return null
  }
  const afterAt = before.slice(at + 1)
  if (/[\s\n\r]/.test(afterAt)) return null
  return { start: at, query: afterAt }
}

const uniqSortedWeak = new WeakMap()
const uniqSortedFp = new Map()
const UNIQ_FP_CAP = 16
const SCAN_CAP = 4000

const baseSort = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' })

function pathsFingerprint(paths) {
  const n = paths.length
  return `${n}:${paths[0] || ''}:${paths[n - 1] || ''}`
}

function normalizePaths(paths) {
  const arr = Array.isArray(paths) ? paths : []
  const list = []
  for (const p of arr) {
    if (!p) continue
    list.push(String(p).replace(/\\/g, '/'))
  }
  return list
}

function uniquedPaths(list) {
  if (list.length < 200) return [...new Set(list)]
  return list
}

function getUniqSorted(paths) {
  const arr = Array.isArray(paths) ? paths : []
  if (typeof arr === 'object') {
    const hit = uniqSortedWeak.get(arr)
    if (hit) return hit
  }
  const fp = pathsFingerprint(arr)
  const fpHit = uniqSortedFp.get(fp)
  if (fpHit) {
    if (typeof arr === 'object') uniqSortedWeak.set(arr, fpHit)
    return fpHit
  }
  const uniq = uniquedPaths(normalizePaths(arr))
  const sorted = [...uniq].sort(baseSort)
  const packed = { uniq, sorted }
  if (typeof arr === 'object') uniqSortedWeak.set(arr, packed)
  uniqSortedFp.set(fp, packed)
  while (uniqSortedFp.size > UNIQ_FP_CAP) {
    uniqSortedFp.delete(uniqSortedFp.keys().next().value)
  }
  return packed
}

/**
 * Rank paths for @ autocomplete: basename match, path match, segment matches; shallow paths tie-break.
 * @param {string[]} paths
 * @param {string} query
 * @param {number} limit
 * @returns {string[]}
 */
export function rankProjectPathMatches(paths, query, limit = 14) {
  const needle = (query ?? '').trim().toLowerCase()
  const packed = getUniqSorted(paths)
  if (!needle) {
    return packed.sorted.slice(0, limit)
  }
  let uniq = packed.uniq
  if (uniq.length > SCAN_CAP) {
    const pre = []
    for (const rel of uniq) {
      const relL = rel.toLowerCase()
      const slash = relL.lastIndexOf('/')
      const base = slash === -1 ? relL : relL.slice(slash + 1)
      if (base.includes(needle) || relL.includes(needle)) pre.push(rel)
    }
    uniq = pre.length > SCAN_CAP ? pre.slice(0, SCAN_CAP) : pre
  }
  const scored = []
  for (const rel of uniq) {
    const relL = rel.toLowerCase()
    const segments = rel.split('/').filter(Boolean)
    const base = (segments[segments.length - 1] || '').toLowerCase()
    let score = 0
    if (base === needle) score += 1200
    else if (base.startsWith(needle)) score += 950
    else if (base.includes(needle)) score += 600
    if (relL === needle) score += 1100
    else if (
      relL.endsWith(needle) &&
      (relL.length === needle.length || relL[relL.length - needle.length - 1] === '/')
    ) {
      score += 850
    }
    if (relL.includes(needle)) score += 180 + Math.min(120, needle.length * 4)
    for (const seg of segments) {
      const s = seg.toLowerCase()
      if (s === needle) score += 420
      else if (s.startsWith(needle)) score += 260
      else if (s.includes(needle)) score += 120
    }
    score -= Math.min(100, segments.length * 10)
    if (score > 0) scored.push({ rel, score })
  }
  scored.sort((a, b) => b.score - a.score || baseSort(a.rel, b.rel))
  return scored.slice(0, limit).map((x) => x.rel)
}
