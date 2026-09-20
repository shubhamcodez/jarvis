import { useMemo, useState, useEffect } from 'react'

function buildTree(rootLabel, fileRelPaths) {
  const root = { name: rootLabel, path: '', kind: 'dir', children: [] }
  const sorted = [...fileRelPaths].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' }),
  )
  for (const rel of sorted) {
    const parts = rel.split('/').filter(Boolean)
    let node = root
    let acc = []
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isFile = i === parts.length - 1
      acc.push(part)
      const subPath = acc.join('/')
      if (!node.children) node.children = []
      let child = node.children.find((c) => c.name === part)
      if (!child) {
        child = {
          name: part,
          path: subPath,
          kind: isFile ? 'file' : 'dir',
          children: isFile ? undefined : [],
        }
        node.children.push(child)
      } else if (!isFile && child.kind === 'file') {
        child.kind = 'dir'
        child.children = child.children || []
      }
      node = child
    }
  }
  function sortCh(n) {
    if (!n.children) return
    n.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
    })
    n.children.forEach(sortCh)
  }
  sortCh(root)
  return root
}

function FileTreeRows({ nodes, depth, expanded, toggle, onFileOpen }) {
  return nodes.map((node) => (
    <div key={node.path || node.name}>
      <div
        className={`repo-file-tree__row${node.kind === 'file' ? ' repo-file-tree__row--file' : ''}`}
        style={{ paddingLeft: `${10 + depth * 14}px` }}
      >
        {node.kind === 'dir' ? (
          <button
            type="button"
            className="repo-file-tree__chev"
            onClick={() => toggle(node.path)}
            aria-expanded={expanded.has(node.path)}
            aria-label={expanded.has(node.path) ? 'Collapse folder' : 'Expand folder'}
          >
            {expanded.has(node.path) ? '▼' : '▶'}
          </button>
        ) : (
          <span className="repo-file-tree__chev-spacer" aria-hidden />
        )}
        {node.kind === 'file' && onFileOpen ? (
          <button
            type="button"
            className="repo-file-tree__file-btn"
            onClick={() => onFileOpen(node.path)}
            title={`Open ${node.name} in editor`}
          >
            {node.name}
          </button>
        ) : (
          <span
            className={
              node.kind === 'dir'
                ? 'repo-file-tree__name repo-file-tree__name--dir'
                : 'repo-file-tree__name repo-file-tree__name--file'
            }
          >
            {node.name}
          </span>
        )}
      </div>
      {node.kind === 'dir' && expanded.has(node.path) && node.children?.length ? (
        <FileTreeRows
          nodes={node.children}
          depth={depth + 1}
          expanded={expanded}
          toggle={toggle}
          onFileOpen={onFileOpen}
        />
      ) : null}
    </div>
  ))
}

/** VS Code–style explorer for indexed project files (browser-opened folder). */
export function ProjectFileTree({ rootLabel, relPaths, onFileOpen }) {
  const tree = useMemo(() => {
    if (!rootLabel?.trim() || !relPaths?.length) return null
    return buildTree(rootLabel.trim(), relPaths)
  }, [rootLabel, relPaths])

  const [expanded, setExpanded] = useState(() => new Set(['']))

  useEffect(() => {
    if (!tree?.children?.length) return
    const s = new Set([''])
    for (const c of tree.children) {
      if (c.kind === 'dir') s.add(c.path)
    }
    setExpanded(s)
  }, [rootLabel])

  const toggle = (path) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  if (!tree?.children?.length) return null

  return (
    <div className="repo-file-tree" aria-label="Project files">
      <div className="repo-file-tree__toolbar" role="presentation">
        <span className="repo-file-tree__toolbar-title">Explorer</span>
        <span className="repo-file-tree__count">{relPaths.length} files</span>
      </div>
      <div className="repo-file-tree__scroll">
        <div className="repo-file-tree__root-row">
          <button
            type="button"
            className="repo-file-tree__chev repo-file-tree__chev--root"
            onClick={() => toggle('')}
            aria-expanded={expanded.has('')}
            aria-label={expanded.has('') ? 'Collapse project root' : 'Expand project root'}
          >
            {expanded.has('') ? '▼' : '▶'}
          </button>
          <span className="repo-file-tree__root-name">{tree.name}</span>
        </div>
        {expanded.has('') ? (
          <FileTreeRows nodes={tree.children} depth={0} expanded={expanded} toggle={toggle} onFileOpen={onFileOpen} />
        ) : null}
      </div>
    </div>
  )
}
