/** Parse / strip ```jarvis-file:...``` / ```ada-file:...``` blocks. */

const OPEN = /^```\s*(?:jarvis|ada)-file:([^\n`]+)\s*$/
const CLOSE = /^```\s*$/

function splitKeepEnds(text) {
  const src = String(text ?? '')
  const lines = []
  let start = 0
  for (let i = 0; i < src.length; i += 1) {
    if (src[i] === '\n') {
      lines.push(src.slice(start, i + 1))
      start = i + 1
    }
  }
  if (start < src.length) lines.push(src.slice(start))
  return lines
}

function normalizeRel(raw) {
  const p = String(raw || '').trim().replace(/\\/g, '/').replace(/^\/+/, '')
  if (!p || p.startsWith('..') || `/${p}/`.includes('/../')) return null
  if (p.split('/').includes('..')) return null
  if (/^[A-Za-z]:/.test(p) || p.startsWith('//')) return null
  return p
}

/**
 * @param {string} text
 * @returns {{ clean: string, edits: Array<{ path: string, content: string }> }}
 */
export function extractWorkspaceFileEdits(text) {
  if (!text) return { clean: '', edits: [] }
  const byPath = new Map()
  const out = []
  const lines = splitKeepEnds(text)
  let i = 0
  while (i < lines.length) {
    const raw = lines[i]
    const opened = OPEN.exec(raw.replace(/\r?\n$/, ''))
    if (!opened) {
      out.push(raw)
      i += 1
      continue
    }
    const rel = normalizeRel(opened[1])
    const bodyParts = []
    i += 1
    let closed = false
    while (i < lines.length) {
      const bline = lines[i]
      const bstrip = bline.replace(/\r?\n$/, '')
      if (CLOSE.test(bstrip)) {
        closed = true
        i += 1
        break
      }
      if (bstrip.endsWith('```')) {
        bodyParts.push(bstrip.slice(0, -3))
        closed = true
        i += 1
        break
      }
      bodyParts.push(bline)
      i += 1
    }
    if (!closed) {
      out.push(raw)
      out.push(...bodyParts)
      continue
    }
    if (rel) byPath.set(rel, bodyParts.join(''))
    // #region agent log
    fetch('http://127.0.0.1:7379/ingest/d4a6c664-f167-437c-bb75-f8687c530271',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'ff2cb7'},body:JSON.stringify({sessionId:'ff2cb7',location:'workspaceFileEdits.js:extract',message:'client extract fence',data:{path:rel,closed,edits:byPath.size},timestamp:Date.now(),hypothesisId:'F'})}).catch(()=>{})
    // #endregion
  }
  const clean = out.join('').replace(/\n{3,}/g, '\n\n').trim()
  const edits = [...byPath.entries()].map(([path, content]) => ({ path, content }))
  return { clean, edits }
}

export function stripJarvisFileFencesForDisplay(text) {
  if (!text) return ''
  return extractWorkspaceFileEdits(text).clean
}
