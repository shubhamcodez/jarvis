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

/**
 * Rank paths for @ autocomplete: basename match, path match, segment matches; shallow paths tie-break.
 * @param {string[]} paths
 * @param {string} query
 * @param {number} limit
 * @returns {string[]}
 */
export function rankProjectPathMatches(paths, query, limit = 14) {
  const needle = (query ?? '').trim().toLowerCase()
  const uniq = [...new Set(paths.filter(Boolean))]
  const baseSort = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' })
  if (!needle) {
    return [...uniq].sort(baseSort).slice(0, limit)
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
