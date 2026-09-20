import { useMemo, useState, useEffect } from 'react'

function buildTree(rootLabel, fileRelPaths) {
  const root = { name: rootLabel, path: '', kind: 'dir', children: [] }
  const childMaps = new WeakMap()
  const childByName = (parent, name) => childMaps.get(parent)?.get(name)
  const rememberChild = (parent, child) => {
    let m = childMaps.get(parent)
    if (!m) {
      m = new Map()
      childMaps.set(parent, m)
    }
    m.set(child.name, child)
  }
  const sorted = [...fileRelPaths].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' }),
  )
  for (const raw of sorted) {
    const isDirPath = String(raw).endsWith('/')
    const parts = String(raw)
      .replace(/\/+$/, '')
      .split('/')
      .filter(Boolean)
    if (!parts.length) continue
    let node = root
    let acc = []
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isFile = !isDirPath && i === parts.length - 1
      acc.push(part)
      const subPath = acc.join('/')
      if (!node.children) node.children = []
      let child = childByName(node, part)
      if (!child) {
        child = {
          name: part,
          path: subPath,
          kind: isFile ? 'file' : 'dir',
          children: isFile ? undefined : [],
        }
        node.children.push(child)
        rememberChild(node, child)
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

function fileGlyph(name) {
  const base = String(name).toLowerCase()
  if (base === '.env' || base.startsWith('.env.')) return { kind: 'env', label: '⚙' }
  if (base === '.gitignore' || base === '.gitattributes') return { kind: 'git', label: '⎇' }
  if (base.endsWith('.md')) return { kind: 'md', label: 'M' }
  if (base.endsWith('.json') || base.endsWith('.jsonc')) return { kind: 'json', label: '{}' }
  if (base.endsWith('.py')) return { kind: 'py', label: 'Py' }
  if (/\.(jsx|tsx|js|ts|mjs|cjs)$/.test(base)) return { kind: 'js', label: 'JS' }
  if (/\.(css|scss|sass|less)$/.test(base)) return { kind: 'css', label: '#' }
  if (/\.(html|htm)$/.test(base)) return { kind: 'html', label: '</>' }
  if (/\.(png|jpe?g|gif|webp|svg|ico|bmp)$/.test(base)) return { kind: 'img', label: '▣' }
  if (/\.(ya?ml)$/.test(base)) return { kind: 'yml', label: 'Y' }
  if (base.endsWith('.toml')) return { kind: 'toml', label: 'T' }
  if (base === 'dockerfile' || base.endsWith('.dockerfile')) return { kind: 'docker', label: '▣' }
  return { kind: 'file', label: '≡' }
}

function FolderIcon({ open }) {
  return (
    <span className={`repo-file-tree__icon repo-file-tree__icon--folder${open ? ' is-open' : ''}`} aria-hidden>
      <svg viewBox="0 0 16 16" width="16" height="16">
        {open ? (
          <path
            fill="currentColor"
            d="M1.5 3.5A1.5 1.5 0 0 1 3 2h3.17a1.5 1.5 0 0 1 1.06.44L8.5 3.7h4A1.5 1.5 0 0 1 14 5.2v.4l-.7 6.1A1.5 1.5 0 0 1 11.81 13H3.7A1.5 1.5 0 0 1 2.2 11.7L1.5 5.5z"
          />
        ) : (
          <path
            fill="currentColor"
            d="M1.75 3A1.75 1.75 0 0 0 0 4.75v7.5C0 13.22.78 14 1.75 14h12.5A1.75 1.75 0 0 0 16 12.25v-6.5A1.75 1.75 0 0 0 14.25 4H8.31l-.72-.72A1.75 1.75 0 0 0 6.35 2.75H1.75z"
          />
        )}
      </svg>
    </span>
  )
}

function FileIcon({ name }) {
  const g = fileGlyph(name)
  return (
    <span className={`repo-file-tree__icon repo-file-tree__icon--${g.kind}`} aria-hidden>
      {g.label}
    </span>
  )
}

function FileTreeRows({ nodes, depth, expanded, toggle, onFileOpen, activePath }) {
  return nodes.map((node) => {
    const open = node.kind === 'dir' && expanded.has(node.path)
    const active = node.kind === 'file' && activePath === node.path
    return (
      <div key={node.path || node.name}>
        <div
          className={`repo-file-tree__row${node.kind === 'file' ? ' repo-file-tree__row--file' : ' repo-file-tree__row--dir'}${active ? ' is-active' : ''}`}
          style={{ paddingLeft: `${8 + depth * 12}px` }}
          role="treeitem"
          aria-expanded={node.kind === 'dir' ? open : undefined}
          aria-selected={active || undefined}
          onClick={() => {
            if (node.kind === 'dir') toggle(node.path)
            else onFileOpen?.(node.path)
          }}
          title={node.path || node.name}
        >
          {node.kind === 'dir' ? (
            <span className="repo-file-tree__chev" aria-hidden>
              {open ? '▼' : '▶'}
            </span>
          ) : (
            <span className="repo-file-tree__chev-spacer" aria-hidden />
          )}
          {node.kind === 'dir' ? <FolderIcon open={open} /> : <FileIcon name={node.name} />}
          <span
            className={
              node.kind === 'dir'
                ? 'repo-file-tree__name repo-file-tree__name--dir'
                : 'repo-file-tree__name repo-file-tree__name--file'
            }
          >
            {node.name}
          </span>
        </div>
        {node.kind === 'dir' && open && node.children?.length ? (
          <FileTreeRows
            nodes={node.children}
            depth={depth + 1}
            expanded={expanded}
            toggle={toggle}
            onFileOpen={onFileOpen}
            activePath={activePath}
          />
        ) : null}
      </div>
    )
  })
}

/** VS Code–style explorer for indexed / linked project files. */
export function ProjectFileTree({ rootLabel, relPaths, onFileOpen, activePath }) {
  const relSig =
    relPaths?.length
      ? `${relPaths.length}:${relPaths.slice(0, 3).join('|')}:${relPaths.slice(-3).join('|')}`
      : ''
  const tree = useMemo(() => {
    if (!rootLabel?.trim() || !relPaths?.length) return null
    return buildTree(rootLabel.trim(), relPaths)
  }, [rootLabel, relPaths, relSig])

  const [expanded, setExpanded] = useState(() => new Set(['']))

  useEffect(() => {
    setExpanded(new Set(['']))
  }, [rootLabel])

  const toggle = (path) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  const fileCount = useMemo(
    () => (relPaths || []).filter((p) => !String(p).endsWith('/')).length,
    [relPaths],
  )

  if (!tree?.children?.length) return null

  return (
    <div className="repo-file-tree" aria-label="Project files" role="tree">
      <div className="repo-file-tree__toolbar" role="presentation">
        <span className="repo-file-tree__toolbar-title">Explorer</span>
        <span className="repo-file-tree__count">{fileCount} files</span>
      </div>
      <div className="repo-file-tree__scroll">
        <div
          className="repo-file-tree__root-row"
          role="treeitem"
          aria-expanded={expanded.has('')}
          onClick={() => toggle('')}
        >
          <span className="repo-file-tree__chev repo-file-tree__chev--root" aria-hidden>
            {expanded.has('') ? '▼' : '▶'}
          </span>
          <FolderIcon open={expanded.has('')} />
          <span className="repo-file-tree__root-name">{tree.name}</span>
        </div>
        {expanded.has('') ? (
          <FileTreeRows
            nodes={tree.children}
            depth={0}
            expanded={expanded}
            toggle={toggle}
            onFileOpen={onFileOpen}
            activePath={activePath}
          />
        ) : null}
      </div>
    </div>
  )
}
