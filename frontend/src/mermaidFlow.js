/** Tiny flowchart renderer for ```mermaid graph/flowchart fences (no CDN). */

function unwrap(raw) {
  return String(raw ?? '').replace(/^\n/, '').replace(/\n$/, '')
}

function takeNode(id, label, shape, nodes) {
  if (!id) return
  const prev = nodes.get(id)
  nodes.set(id, {
    id,
    label: label || prev?.label || id,
    shape: shape || prev?.shape || 'rect',
  })
}

function parseNodeToken(tok) {
  const t = (tok || '').trim()
  let m = t.match(/^([A-Za-z][\w-]*)\[([^\]]+)\]$/)
  if (m) return { id: m[1], label: m[2], shape: 'rect' }
  m = t.match(/^([A-Za-z][\w-]*)\(([^\)]+)\)$/)
  if (m) return { id: m[1], label: m[2], shape: 'round' }
  m = t.match(/^([A-Za-z][\w-]*)\{([^}]+)\}$/)
  if (m) return { id: m[1], label: m[2], shape: 'diamond' }
  m = t.match(/^([A-Za-z][\w-]*)$/)
  if (m) return { id: m[1], label: '', shape: '' }
  return null
}

export function parseMermaidFlow(src) {
  const lines = unwrap(src)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('%%'))
  if (!lines.length) return null
  const header = lines[0]
  if (!/^(graph|flowchart)\b/i.test(header)) return null
  const dir = (/(\bLR\b|\bRL\b|\bTB\b|\bTD\b|\bBT\b)/i.exec(header) || ['TD'])[0].toUpperCase()
  const afterDir = header
    .replace(/^(graph|flowchart)\b/i, '')
    .replace(/^\s*(?:LR|RL|TB|TD|BT)\b/i, '')
    .replace(/^[;\s]+/, '')
  const inline = afterDir
    .split(';')
    .map((s) => s.trim())
    .filter(Boolean)
  const nodes = new Map()
  const edges = []
  for (const line of [...inline, ...lines.slice(1)]) {
    const edge = line.match(
      /^(.+?)\s*-->(?:\|([^|]+)\|)?\s*(.+)$/,
    )
    if (edge) {
      const a = parseNodeToken(edge[1])
      const b = parseNodeToken(edge[3])
      if (!a || !b) continue
      takeNode(a.id, a.label, a.shape, nodes)
      takeNode(b.id, b.label, b.shape, nodes)
      edges.push({ from: a.id, to: b.id, label: (edge[2] || '').trim() })
      continue
    }
    const only = parseNodeToken(line)
    if (only) takeNode(only.id, only.label, only.shape, nodes)
  }
  if (!nodes.size) return null
  const parsed = { dir: dir === 'TD' ? 'TB' : dir, nodes: [...nodes.values()], edges }
  // #region agent log
  fetch('http://127.0.0.1:7379/ingest/d4a6c664-f167-437c-bb75-f8687c530271',{method:'POST',headers:{'Content-Type':'application/json','X-Debug-Session-Id':'ff2cb7'},body:JSON.stringify({sessionId:'ff2cb7',location:'mermaidFlow.js:parseMermaidFlow',message:'parsed mermaid',data:{dir:parsed.dir,nodeCount:parsed.nodes.length,edgeCount:parsed.edges.length,inlineCount:inline.length},timestamp:Date.now(),hypothesisId:'D'})}).catch(()=>{})
  // #endregion
  return parsed
}

function mermaidUnsupportedSvg(src) {
  const first = unwrap(src).split(/\r?\n/).find((l) => l.trim()) || 'mermaid'
  const kind = first.trim().split(/\s+/)[0] || 'diagram'
  const hint = /^(graph|flowchart)\b/i.test(first)
    ? 'Could not parse this flowchart.'
    : `${kind} diagrams are not rendered locally. Jarvis previews graph/flowchart fences.`
  const w = 420
  const h = 88
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect width="${w}" height="${h}" fill="#f7f7f4" stroke="#bbb"/><text x="16" y="36" font-size="13" font-family="sans-serif" fill="#333">${escapeXml(hint)}</text><text x="16" y="60" font-size="11" font-family="sans-serif" fill="#666">${escapeXml(first.slice(0, 64))}</text></svg>`
}

export function mermaidFlowSvg(src) {
  const g = parseMermaidFlow(src)
  if (!g) return mermaidUnsupportedSvg(src)
  const horizontal = g.dir === 'LR' || g.dir === 'RL'
  const rank = new Map()
  const incoming = new Map(g.nodes.map((n) => [n.id, 0]))
  for (const e of g.edges) incoming.set(e.to, (incoming.get(e.to) || 0) + 1)
  const roots = g.nodes.filter((n) => !incoming.get(n.id)).map((n) => n.id)
  const q = roots.length ? roots : [g.nodes[0].id]
  for (const id of q) if (!rank.has(id)) rank.set(id, 0)
  for (let i = 0; i < q.length; i += 1) {
    const id = q[i]
    const r = rank.get(id) || 0
    for (const e of g.edges.filter((x) => x.from === id)) {
      const next = Math.max(rank.get(e.to) || 0, r + 1)
      if (!rank.has(e.to) || next > rank.get(e.to)) {
        rank.set(e.to, next)
        q.push(e.to)
      }
    }
  }
  const byRank = new Map()
  for (const n of g.nodes) {
    const r = rank.get(n.id) || 0
    if (!byRank.has(r)) byRank.set(r, [])
    byRank.get(r).push(n)
  }
  const ranks = [...byRank.keys()].sort((a, b) => a - b)
  const colW = 160
  const rowH = 70
  const pos = new Map()
  ranks.forEach((r, ri) => {
    const row = byRank.get(r)
    row.forEach((n, i) => {
      const x = horizontal ? 40 + ri * colW : 40 + i * colW
      const y = horizontal ? 40 + i * rowH : 40 + ri * rowH
      pos.set(n.id, { x, y })
    })
  })
  const maxX = Math.max(...[...pos.values()].map((p) => p.x), 40)
  const maxY = Math.max(...[...pos.values()].map((p) => p.y), 40)
  const w = maxX + 140
  const h = maxY + 80
  const nodeSvg = g.nodes
    .map((n) => {
      const p = pos.get(n.id)
      const label = (n.label || n.id).slice(0, 28)
      if (n.shape === 'diamond') {
        return `<polygon points="${p.x + 50},${p.y} ${p.x + 100},${p.y + 18} ${p.x + 50},${p.y + 36} ${p.x},${p.y + 18}" fill="#eef6ff" stroke="#3b6ea5"/><text x="${p.x + 50}" y="${p.y + 22}" text-anchor="middle" font-size="11" font-family="sans-serif">${escapeXml(label)}</text>`
      }
      const rx = n.shape === 'round' ? 16 : 6
      return `<rect x="${p.x}" y="${p.y}" width="100" height="36" rx="${rx}" fill="#f7f7f4" stroke="#444"/><text x="${p.x + 50}" y="${p.y + 23}" text-anchor="middle" font-size="11" font-family="sans-serif">${escapeXml(label)}</text>`
    })
    .join('')
  const edgeSvg = g.edges
    .map((e) => {
      const a = pos.get(e.from)
      const b = pos.get(e.to)
      if (!a || !b) return ''
      const x1 = a.x + 50
      const y1 = a.y + 36
      const x2 = b.x + 50
      const y2 = b.y
      const mid = e.label
        ? `<text x="${(x1 + x2) / 2}" y="${(y1 + y2) / 2 - 4}" text-anchor="middle" font-size="10" fill="#555">${escapeXml(e.label)}</text>`
        : ''
      return `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#666" marker-end="url(#arr)"/>${mid}`
    })
    .join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs><marker id="arr" markerWidth="8" markerHeight="8" refX="8" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8 z" fill="#666"/></marker></defs>${edgeSvg}${nodeSvg}</svg>`
}

function escapeXml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
