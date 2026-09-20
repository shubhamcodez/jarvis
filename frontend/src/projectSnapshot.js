/**
 * Build the same style of markdown snapshot as backend tools/project_repository.py,
 * from files the user selected in the browser (no server path required).
 */

const SKIP_DIR_NAMES = new Set([
  '.git',
  '.svn',
  '.hg',
  'node_modules',
  '__pycache__',
  '.venv',
  'venv',
  '.tox',
  '.mypy_cache',
  '.ruff_cache',
  '.pytest_cache',
  'dist',
  'build',
  '.next',
  'out',
  'coverage',
  'target',
  '.idea',
  '.vs',
  '__MACOSX',
  '.gradle',
  '.cargo',
])

const TEXT_SUFFIXES = new Set([
  '.py',
  '.pyi',
  '.md',
  '.txt',
  '.rst',
  '.json',
  '.yml',
  '.yaml',
  '.toml',
  '.ini',
  '.cfg',
  '.rs',
  '.go',
  '.java',
  '.kt',
  '.c',
  '.h',
  '.cpp',
  '.hpp',
  '.cs',
  '.js',
  '.mjs',
  '.cjs',
  '.ts',
  '.tsx',
  '.jsx',
  '.vue',
  '.svelte',
  '.css',
  '.scss',
  '.sass',
  '.less',
  '.html',
  '.htm',
  '.xml',
  '.sql',
  '.sh',
  '.bash',
  '.zsh',
  '.ps1',
  '.bat',
  '.cmd',
  '.dockerignore',
  '.editorconfig',
  '.gitignore',
  '.gitattributes',
  '.env.example',
])

const SPECIAL_FILENAMES = new Set([
  'dockerfile',
  'makefile',
  'license',
  'copying',
  'gemfile',
  'rakefile',
  'cargo.toml',
  'cargo.lock',
  'pyproject.toml',
  'poetry.lock',
  'composer.json',
  'package.json',
  'go.mod',
  'go.sum',
])

const MAX_INDEX_LINES = 1200
const MAX_DEPTH = 14
const MAX_FILES_WITH_BODY = 120
const MAX_BYTES_PER_FILE = 36_000
const MAX_TOTAL_SNAPSHOT_CHARS = 48_000

/**
 * Fair excerpt order: plain sort fills slots with paths starting with "a"–"v" first, starving
 * later folders (e.g. websitev2/). Round-robin one file per top-level segment per pass.
 * @param {{ rel: string, file: File }[]} items must be pre-filtered / sorted within buckets as needed
 */
function interleaveByTopLevelSegment(items) {
  const groups = new Map()
  for (const item of items) {
    const parts = item.rel.replace(/\\/g, '/').split('/').filter(Boolean)
    const key = parts[0] ?? ''
    if (!groups.has(key)) groups.set(key, [])
    groups.get(key).push(item)
  }
  for (const arr of groups.values()) {
    arr.sort((a, b) => a.rel.localeCompare(b.rel, undefined, { sensitivity: 'base' }))
  }
  const keys = [...groups.keys()].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
  const out = []
  let progress = true
  while (progress) {
    progress = false
    for (const k of keys) {
      const arr = groups.get(k)
      if (arr?.length) {
        out.push(arr.shift())
        progress = true
      }
    }
  }
  return out
}

function shouldSkipRelativePath(rel) {
  const parts = rel.split('/').filter(Boolean)
  if (parts.length > MAX_DEPTH + 1) return true
  for (let i = 0; i < parts.length - 1; i++) {
    const p = parts[i]
    if (SKIP_DIR_NAMES.has(p) || p.startsWith('.')) return true
  }
  return false
}

export function wantFileBody(rel) {
  const base = rel.split('/').pop()?.toLowerCase() || ''
  if (SPECIAL_FILENAMES.has(base)) return true
  const dot = base.lastIndexOf('.')
  const suf = dot >= 0 ? base.slice(dot) : ''
  return TEXT_SUFFIXES.has(suf)
}

/** @param {File} file */
export async function readFileAsUtf8Limited(file, limit) {
  const slice = file.slice(0, limit)
  const buf = await slice.arrayBuffer()
  const bytes = new Uint8Array(buf)
  const scan = bytes.subarray(0, Math.min(bytes.length, 8192))
  let nul = 0
  for (let i = 0; i < scan.length; i++) if (scan[i] === 0) nul++
  if (nul > 8 || (nul > 0 && file.size > 500_000)) return ''
  const dec = new TextDecoder('utf-8', { fatal: false })
  return dec.decode(bytes).replace(/\0/g, '\uFFFD')
}

async function readTextLimitedInternal(file, limit) {
  return readFileAsUtf8Limited(file, limit)
}

async function filePeekLine(file) {
  const slice = file.slice(0, 512)
  const buf = await slice.arrayBuffer()
  const bytes = new Uint8Array(buf)
  if (bytes.indexOf(0) !== -1 && bytes.indexOf(0) < 2048) return '[binary]'
  const text = new TextDecoder('utf-8', { fatal: false }).decode(bytes)
  const line = text.split(/\r?\n/)[0]?.trim() || ''
  if (!line && file.size === 0) return '[empty]'
  if (!line) return '[empty line]'
  return line.length > 120 ? `${line.slice(0, 117)}...` : line
}

/** @param {{ rel: string, file: File }[]} records rel paths without root prefix */
async function assembleMarkdown(rootLabel, rootOnDiskLine, prefix, records) {
  const sorted = [...records].sort((a, b) => a.rel.localeCompare(b.rel, undefined, { sensitivity: 'base' }))
  const dirSet = new Set()
  for (const { rel } of sorted) {
    const parts = rel.split('/').filter(Boolean)
    for (let i = 0; i < parts.length - 1; i++) {
      dirSet.add(`${parts.slice(0, i + 1).join('/')}/`)
    }
  }
  const subdirLinesFull = Array.from(dirSet)
    .sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
    .map((d) => `${prefix}${d}`)

  const fileMetaFull = []
  for (const { rel, file } of sorted) {
    const base = rel.split('/').pop() || ''
    if (base.startsWith('.')) continue
    const posix = rel
    const peek = file.size === 0 ? '[empty]' : await filePeekLine(file)
    const sizeNote = `${file.size} bytes`
    fileMetaFull.push({
      sort: posix.toLowerCase(),
      line: `${prefix}${posix} | ${sizeNote} | ${peek}`,
      rel: posix,
      file,
    })
  }
  fileMetaFull.sort((a, b) => a.sort.localeCompare(b.sort))

  let subdirLines = subdirLinesFull
  let fileMeta = fileMetaFull
  let truncatedTree = subdirLinesFull.length + fileMetaFull.length > MAX_INDEX_LINES
  if (truncatedTree) {
    const budget = MAX_INDEX_LINES
    subdirLines = subdirLinesFull.slice(0, budget)
    const rest = Math.max(0, budget - subdirLines.length)
    fileMeta = fileMetaFull.slice(0, rest)
  }

  const lines = [
    '# Imported project repository',
    `**Root on disk:** ${rootOnDiskLine}`,
    `**Tree root label:** \`${rootLabel}\``,
    '',
    '_(Index is a path tree + file stats/peek; excerpts follow. Sandbox Python cannot open files on disk.)_',
    '',
    '## Repository tree (recursive index)',
    '',
    prefix,
    '',
  ]

  for (const row of subdirLines) lines.push(row)
  if (subdirLines.length) lines.push('')

  for (const { line } of fileMeta) lines.push(line)

  if (truncatedTree) {
    lines.push('')
    lines.push('_(Tree index truncated; narrow the folder or exclude large trees.)_')
  }

  lines.push('', '## File excerpts', '')

  let used = lines.join('\n').length + lines.length
  let bodies = 0
  const excerptEligible = sorted
    .map(({ rel, file }) => ({ rel, file }))
    .filter(({ rel }) => wantFileBody(rel))
  const excerptCandidates = interleaveByTopLevelSegment(excerptEligible)

  for (const { rel, file } of excerptCandidates) {
    if (bodies >= MAX_FILES_WITH_BODY) {
      lines.push('_(More files omitted; excerpt limit reached.)_')
      break
    }
    if (!wantFileBody(rel)) continue
    const text = (await readTextLimitedInternal(file, MAX_BYTES_PER_FILE)).trim()
    if (!text) continue
    const heading = `### \`${prefix}${rel}\``
    const chunk = `${heading}\n\n\`\`\`\n${text}\n\`\`\`\n\n`
    if (used + chunk.length > MAX_TOTAL_SNAPSHOT_CHARS) {
      lines.push('_(Snapshot size limit reached; remaining files omitted.)_')
      break
    }
    lines.push(chunk)
    used += chunk.length + 1
    bodies += 1
  }

  let out = lines.join('\n').trim()
  if (out.length > MAX_TOTAL_SNAPSHOT_CHARS) {
    out = `${out.slice(0, MAX_TOTAL_SNAPSHOT_CHARS - 20).trim()}\n\n_(truncated)_`
  }
  return out
}

/** Sorted unique relative paths (from project root) for one file per path. */
export function relPathsFromRecords(records) {
  return [...new Set(records.map((r) => r.rel))].sort((a, b) =>
    a.localeCompare(b, undefined, { sensitivity: 'base' }),
  )
}

/**
 * Recover file paths from an existing markdown snapshot (sessions before ada-workspace-paths existed).
 * @param {string} snapshot
 * @param {string} rootLabel must match **Tree root label** in the snapshot
 */
/** Reads **Tree root label:** from an imported snapshot (for excerpt matching after label drift). */
export function parseSnapshotTreeRootLabel(snapshot) {
  if (!snapshot?.trim()) return null
  const m = snapshot.match(/\*\*Tree root label:\*\*\s*`([^`]+)`/i)
  const label = m?.[1]?.trim()
  return label || null
}

export function parseRelPathsFromSnapshotMarkdown(snapshot, rootLabel) {
  if (!snapshot?.trim() || !rootLabel) return []
  const idx = snapshot.indexOf('## Repository tree')
  if (idx === -1) return []
  const rest = snapshot.slice(idx)
  const endIdx = rest.indexOf('## File excerpts')
  const section = endIdx === -1 ? rest : rest.slice(0, endIdx)
  const prefix = `${rootLabel}/`
  const paths = []
  for (const line of section.split('\n')) {
    const t = line.trim()
    if (!t.startsWith(prefix)) continue
    const pipe = t.indexOf(' | ')
    if (pipe === -1) continue
    const rel = t.slice(prefix.length, pipe).trim()
    if (rel) paths.push(rel)
  }
  return [...new Set(paths)].sort((a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' }))
}

export const PREVIEW_MAX_BYTES = 400_000
/** Cap for in-browser image preview (data URLs). */
export const IMAGE_PREVIEW_MAX_BYTES = 6_000_000

/** Paths treated as previewable images in the project explorer. */
export function isProjectImagePath(relPath) {
  const base = (String(relPath).split(/[/\\]/).pop() || '').toLowerCase()
  return /\.(png|jpe?g|gif|webp|svg|bmp|ico)$/i.test(base)
}

function guessImageMimeFromPath(relPath) {
  const base = (String(relPath).split(/[/\\]/).pop() || '').toLowerCase()
  const dot = base.lastIndexOf('.')
  const ext = dot >= 0 ? base.slice(dot) : ''
  const map = {
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.svg': 'image/svg+xml',
    '.bmp': 'image/bmp',
    '.ico': 'image/x-icon',
  }
  return map[ext] || 'application/octet-stream'
}

function arrayBufferToBase64(buffer) {
  let binary = ''
  const bytes = new Uint8Array(buffer)
  const chunk = 8192
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, Math.min(i + chunk, bytes.length)))
  }
  return btoa(binary)
}

async function getProjectFileNativeFile(dirHandle, relPath) {
  if (!dirHandle || !relPath) return null
  const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean)
  if (!parts.length) return null
  let cur = dirHandle
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i]
    const last = i === parts.length - 1
    const kind = last ? 'file' : 'directory'
    const resolved = await getChildHandleCaseInsensitive(cur, seg, kind)
    if (!resolved) return null
    if (last) {
      try {
        return await resolved.handle.getFile()
      } catch {
        return null
      }
    }
    cur = resolved.handle
  }
  return null
}

/**
 * Build a data URL for <img src> from a File (used by disk read and IDB preview cache).
 * @param {File} file
 * @param {string} relPath relative path for extension → MIME
 * @param {number} maxBytes
 * @returns {Promise<string|null>}
 */
export async function fileToImageDataUrl(file, relPath, maxBytes = IMAGE_PREVIEW_MAX_BYTES) {
  if (!file || !relPath || !isProjectImagePath(relPath)) return null
  try {
    const n = Math.min(file.size, maxBytes)
    const slice = file.slice(0, n)
    const buf = await slice.arrayBuffer()
    const mime = guessImageMimeFromPath(relPath)
    if (mime === 'image/svg+xml') {
      const text = new TextDecoder('utf-8', { fatal: false }).decode(buf)
      return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(text)}`
    }
    if (mime === 'application/octet-stream') return null
    return `data:${mime};base64,${arrayBufferToBase64(buf)}`
  } catch {
    return null
  }
}

/**
 * Read an image (or SVG) as a data URL for <img src>.
 * @param {FileSystemDirectoryHandle} dirHandle
 * @param {string} relPath
 * @param {number} maxBytes
 * @returns {Promise<string|null>}
 */
export async function readProjectFileAsDataUrl(dirHandle, relPath, maxBytes = IMAGE_PREVIEW_MAX_BYTES) {
  if (!dirHandle || !relPath || !isProjectImagePath(relPath)) return null
  try {
    const file = await getProjectFileNativeFile(dirHandle, relPath)
    if (!file) return null
    return fileToImageDataUrl(file, relPath, maxBytes)
  } catch {
    return null
  }
}

/**
 * Resolve a child name when filesystem casing may differ (e.g. Windows / picked folder).
 * @param {'file'|'directory'} kind
 */
async function getChildHandleCaseInsensitive(dirHandle, segment, kind) {
  const want = segment.toLowerCase()
  try {
    if (kind === 'file') {
      const fh = await dirHandle.getFileHandle(segment)
      return { type: 'file', handle: fh }
    }
    const dh = await dirHandle.getDirectoryHandle(segment)
    return { type: 'dir', handle: dh }
  } catch {
    /* fall through */
  }
  for await (const [name, handle] of dirHandle.entries()) {
    if (name.toLowerCase() !== want) continue
    if (kind === 'file' && handle.kind === 'file') return { type: 'file', handle }
    if (kind === 'directory' && handle.kind === 'directory') return { type: 'dir', handle }
  }
  return null
}

/**
 * Read a file from a directory picked via showDirectoryPicker (same tree rules as indexing).
 * @param {FileSystemDirectoryHandle} dirHandle
 * @param {string} relPath posix relative path from root
 */
export async function readProjectFileText(dirHandle, relPath, maxBytes = PREVIEW_MAX_BYTES) {
  if (!dirHandle || !relPath) return ''
  const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean)
  if (!parts.length) return ''
  let cur = dirHandle
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i]
    const last = i === parts.length - 1
    const kind = last ? 'file' : 'directory'
    const resolved = await getChildHandleCaseInsensitive(cur, seg, kind)
    if (!resolved) return null
    if (last) {
      const file = await resolved.handle.getFile()
      return readFileAsUtf8Limited(file, maxBytes)
    }
    cur = resolved.handle
  }
  return ''
}

/**
 * Overwrite a file under a picked directory (requires readwrite permission on the handle).
 * @param {FileSystemDirectoryHandle} dirHandle
 * @param {string} relPath
 * @param {string} text full file content as UTF-8
 * @returns {Promise<{ ok: boolean, error?: string }>}
 */
export async function writeProjectFileText(dirHandle, relPath, text) {
  if (!dirHandle || !relPath) return { ok: false, error: 'Missing folder access or path.' }
  const utf8 = typeof text === 'string' ? text : String(text ?? '')
  const parts = relPath.replace(/\\/g, '/').split('/').filter(Boolean)
  if (!parts.length) return { ok: false, error: 'Invalid path.' }
  let cur = dirHandle
  for (let i = 0; i < parts.length; i++) {
    const seg = parts[i]
    const last = i === parts.length - 1
    const kind = last ? 'file' : 'directory'
    const resolved = await getChildHandleCaseInsensitive(cur, seg, kind)
    if (!resolved) return { ok: false, error: 'Could not find that path on disk.' }
    if (last) {
      let w
      try {
        w = await resolved.handle.createWritable()
        await w.write(utf8)
        await w.close()
      } catch (e) {
        try {
          if (w) await w.abort()
        } catch {
          /* ignore */
        }
        return { ok: false, error: e?.message || String(e) }
      }
      return { ok: true }
    }
    cur = resolved.handle
  }
  return { ok: false, error: 'Internal error.' }
}

function parseMarkdownFenceAfterHeading(body, headingEndIndex) {
  const afterH = body.slice(headingEndIndex)
  const fenceOpen = afterH.indexOf('```')
  if (fenceOpen === -1) return null
  let rest = afterH.slice(fenceOpen + 3)
  const lineEnd = rest.indexOf('\n')
  if (lineEnd !== -1) rest = rest.slice(lineEnd + 1)
  const close = rest.indexOf('```')
  if (close === -1) return rest.trim() || null
  const text = rest.slice(0, close).trim()
  return text || null
}

/** Whether snapshot heading path refers to relPath (rootLabel / relative segments). */
function snapshotPathMatchesRel(fullPathRaw, rootLabel, normRel) {
  const full = String(fullPathRaw).replace(/\\/g, '/').trim()
  const rel = String(normRel).replace(/\\/g, '/').trim()
  const root = String(rootLabel).trim()
  if (!full || !rel) return false
  const prefix = `${root}/`
  const fullLo = full.toLowerCase()
  const relLo = rel.toLowerCase()
  const prefixLo = prefix.toLowerCase()
  if (fullLo === `${prefixLo}${relLo}`) return true
  if (fullLo.startsWith(prefixLo) && full.slice(prefix.length).toLowerCase() === relLo) return true
  if (fullLo.endsWith('/' + relLo) || fullLo === relLo) return true
  const fullSegs = full.split('/').filter(Boolean)
  const relSegs = rel.split('/').filter(Boolean)
  if (!relSegs.length || fullSegs.length < relSegs.length) return false
  for (let i = 0; i < relSegs.length; i++) {
    if (fullSegs[fullSegs.length - relSegs.length + i]?.toLowerCase() !== relSegs[i]?.toLowerCase()) return false
  }
  return true
}

/**
 * Parse a file's fenced code block from the browser-built snapshot (excerpt section only).
 * Scans all ### headings for flexible path match (casing, trailing path segments).
 * @returns {string|null} null if not present
 */
export function extractFileExcerptFromSnapshot(snapshot, rootLabel, relPath) {
  if (!snapshot?.trim() || rootLabel == null || relPath == null) return null
  const normRel = String(relPath).replace(/\\/g, '/').trim()
  if (!normRel) return null
  const exIdx = snapshot.indexOf('## File excerpts')
  if (exIdx === -1) return null
  const body = snapshot.slice(exIdx)
  const root = String(rootLabel).trim()
  /** @type {RegExpMatchArray | null} */
  let m
  const headingRe = /^### `([^`]+)`[ \t]*$/gm
  while ((m = headingRe.exec(body)) !== null) {
    const fullPath = m[1]
    if (!snapshotPathMatchesRel(fullPath, root, normRel)) continue
    const text = parseMarkdownFenceAfterHeading(body, m.index + m[0].length)
    if (text != null && text !== '') return text
  }
  return null
}

/**
 * @param {FileSystemDirectoryHandle} dirHandle
 * @param {string} rootLabel
 * @returns {{ snapshot: string, relPaths: string[] }}
 */
export async function buildSnapshotFromDirectoryHandle(dirHandle, rootLabel) {
  const records = []

  async function walk(handle, pathPrefix, depth) {
    for await (const entry of handle.values()) {
      const name = entry.name
      if (entry.kind === 'directory') {
        if (SKIP_DIR_NAMES.has(name) || name.startsWith('.')) continue
        if (depth >= MAX_DEPTH) continue
        await walk(entry, `${pathPrefix}${name}/`, depth + 1)
      } else {
        if (name.startsWith('.')) continue
        const rel = `${pathPrefix}${name}`.replace(/\/+$/, '')
        if (shouldSkipRelativePath(rel)) continue
        const file = await entry.getFile()
        records.push({ rel, file })
      }
    }
  }

  await walk(dirHandle, '', 0)
  const prefix = `${rootLabel}/`
  const rootOnDiskLine =
    '_(Opened from this computer in the browser — the real path is not exposed to the page.)_'
  const relPaths = relPathsFromRecords(records)
  const snapshot = await assembleMarkdown(rootLabel, rootOnDiskLine, prefix, records)
  try {
    const { cachePreviewFilesFromRecords } = await import('./projectFileCache.js')
    await cachePreviewFilesFromRecords(rootLabel, records)
  } catch {
    /* preview cache optional — quota or unsupported */
  }
  return { snapshot, relPaths }
}

/** @param {FileList | File[]} fileList from <input webkitdirectory> */
/** @returns {{ snapshot: string, relPaths: string[] }} */
export async function buildSnapshotFromFileList(fileList) {
  const files = Array.from(fileList || [])
  if (!files.length) return { snapshot: '', relPaths: [] }

  const first = files[0].webkitRelativePath?.replace(/\\/g, '/') || ''
  if (!first) {
    throw new Error('Folder pick is not supported in this browser (missing webkitRelativePath).')
  }
  const rootLabel = first.split('/')[0]
  const records = []

  for (const file of files) {
    let rel = file.webkitRelativePath?.replace(/\\/g, '/') || ''
    if (!rel.startsWith(`${rootLabel}/`)) continue
    rel = rel.slice(rootLabel.length + 1)
    if (!rel || shouldSkipRelativePath(rel)) continue
    const base = rel.split('/').pop() || ''
    if (base.startsWith('.')) continue
    records.push({ rel, file })
  }

  const prefix = `${rootLabel}/`
  const rootOnDiskLine =
    '_(Opened from this computer in the browser — the real path is not exposed to the page.)_'
  const relPaths = relPathsFromRecords(records)
  const snapshot = await assembleMarkdown(rootLabel, rootOnDiskLine, prefix, records)
  try {
    const { cachePreviewFilesFromRecords } = await import('./projectFileCache.js')
    await cachePreviewFilesFromRecords(rootLabel, records)
  } catch {
    /* ignore */
  }
  return { snapshot, relPaths }
}

export function canUseDirectoryPicker() {
  return typeof window !== 'undefined' && typeof window.showDirectoryPicker === 'function'
}
