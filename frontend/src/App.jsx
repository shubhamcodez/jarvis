import { useState, useEffect, useRef, useCallback, useMemo, forwardRef, useImperativeHandle } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { Prec } from '@codemirror/state'
import { keymap } from '@codemirror/view'
import { languageExtensionsForPath, themeExtensionsForScheme } from './filePreviewCodeMirror'
import {
  listChats,
  setCurrentChat,
  getCurrentChatId,
  readChatLog,
  createNewChat,
  deleteChat,
  sendMessageStream,
  sendMessageWithFiles,
  chatbotResponse,
  appendChatLog,
  getChatsStoragePath,
  setChatsStoragePath,
  getModelSetting,
  setModelSetting,
  getGoogleAuthStatus,
  getGoogleAuthLoginUrl,
  getGmailProfile,
  googleLogout,
  googleDisconnect,
  agentStepsWsUrl,
  initApiAuth,
  runHostShellCommand,
  runWorkspaceFile,
  getUserProfile,
  saveUserProfile,
  getRuntimeSettings,
  setAutonomyLevel,
  setDesktopArmed,
  saveApiKeys,
  getWorkspaceStatus,
  linkWorkspace,
  unlinkWorkspace,
  fetchWorkspaceSnapshot,
  listWorkspaceFiles,
  workspaceTreeStamp,
  readWorkspaceFile,
  writeWorkspaceFile,
  listPendingApprovals,
  resolveAgentApproval,
  pickWorkspaceFolderNative,
  isDesktopShell,
  searchChats,
  compactChat,
  getChatHandoff,
  getChatRecap,
  forkChat,
  mergeChat,
  getChatMeta,
  runSlash,
  rewindChat,
  reactToReply,
  listChatReactions,
  listBookmarks,
  addBookmark,
  deleteBookmark,
  getUsageStats,
  getSlashCatalog,
  setRunMode,
  setSpendLimits,
  setQuietHours,
  listTasks,
  cancelTask,
  listActiveRuns,
  stopActiveRun,
  steerActiveRun,
  listCheckpoints,
  restoreCheckpoint,
  listFacts,
  addFact,
  deleteFact,
  getIdentity,
  saveIdentity,
  getLocalModels,
  getLocalModelJob,
  downloadLocalModel,
  loadLocalModel,
  listCustomAgents,
  createCustomAgent,
} from './api'
import { CustomAgentEditor } from './CustomAgentEditor'
import { CreateAgentModal } from './CreateAgentModal'
import { ControlPlane, ModeMenu } from './ControlPlane'
import {
  buildSnapshotFromDirectoryHandle,
  listTreePathsFromDirectoryHandle,
  buildSnapshotFromFileList,
  canUseDirectoryPicker,
  parseRelPathsFromSnapshotMarkdown,
  readProjectFileText,
  readProjectFileAsDataUrl,
  isProjectImagePath,
  writeProjectFileText,
} from './projectSnapshot'
import {
  saveProjectRootHandleRecord,
  loadProjectRootHandleRecord,
  clearProjectRootHandleRecord,
  ensureDirectoryReadPermission,
  ensureDirectoryReadWritePermission,
} from './projectHandleStorage'
import {
  getPreviewFileText,
  getPreviewImageDataUrl,
  clearPreviewCacheForRoot,
  putPreviewFileText,
} from './projectFileCache'
import { getActiveFileMention, rankProjectPathMatches } from './projectPathSuggest'
import { ProjectFileTree } from './ProjectFileTree'
import { CodingEditSummaryCards } from './CodingEditSummaryCards'
import { WorkspaceFileReview } from './WorkspaceFileReview'
import { stripJarvisFileFencesForDisplay } from './workspaceFileEdits'
import { CHAT_HELP_MANUAL_MARKDOWN } from './chatHelpManual'
import LivePreview, { isPreviewLanguage } from './LivePreview'
import { ensureNotifyPermission, notifyJarvis } from './notify'
import {
  EMPTY_USER_PROFILE,
  INTRODUCE_STEPS,
  INTRODUCE_STEP_COUNT,
  normalizeProfileAnswer,
  setUserProfileField,
  formatIntroduceWelcome,
  formatIntroduceQuestion,
} from './userProfileIntroduce'
import {
  pushWorkspaceEdit,
  workspaceUndoStatus,
  finalizeWorkspaceUndoPop,
  peekWorkspaceRedo,
  finalizeWorkspaceRedoPop,
  clearWorkspaceUndo,
} from './workspaceUndoHistory'
import ReactMarkdown, { defaultUrlTransform } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import './App.css'

const CODING_LAYOUT_STORAGE_KEY = 'jarvis-coding-layout-widths'

const EXPLORER_PANEL = { min: 200, max: 560, default: 280 }
const CHAT_RAIL_PANEL = { min: 280, max: 720, default: 400 }

function clampPanelWidth(n, lo, hi) {
  const x = Number(n)
  if (!Number.isFinite(x)) return lo
  return Math.min(hi, Math.max(lo, x))
}

function readCodingLayoutWidths() {
  try {
    const raw = localStorage.getItem(CODING_LAYOUT_STORAGE_KEY)
    if (raw) {
      const o = JSON.parse(raw)
      return {
        explorer: clampPanelWidth(o.explorer, EXPLORER_PANEL.min, EXPLORER_PANEL.max),
        chatRail: clampPanelWidth(o.chatRail, CHAT_RAIL_PANEL.min, CHAT_RAIL_PANEL.max),
      }
    }
  } catch {
    /* ignore */
  }
  return { explorer: EXPLORER_PANEL.default, chatRail: CHAT_RAIL_PANEL.default }
}

/** Embedded charts from the coding agent sandbox use data:image/... URLs. */
function markdownUrlTransform(url) {
  if (typeof url === 'string' && /^data:image\/(png|jpe?g|gif|webp|svg\+xml);/i.test(url)) return url
  return defaultUrlTransform(url)
}

/** Plain text from ReactMarkdown element children. */
function markdownNodeToPlainText(node) {
  if (node == null || node === false) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(markdownNodeToPlainText).join('')
  if (typeof node === 'object' && node.props?.children != null) {
    return markdownNodeToPlainText(node.props.children)
  }
  return ''
}

/**
 * Match heading or label text to a pending workspace edit path (relative, forward slashes).
 */
function resolveEditFilePathFromHeading(files, rawHeading) {
  if (!files?.length || rawHeading == null) return null
  let t = String(rawHeading).trim().replace(/^#+\s*/, '').replace(/`/g, '').trim().replace(/\\/g, '/')
  if (!t) return null
  if (files.some((f) => f.path === t)) return t
  const bySuffix = files.find((f) => f.path.endsWith('/' + t))
  if (bySuffix) return bySuffix.path
  const wantBase = t.includes('/') ? t.split('/').pop() : t
  const baseHits = files.filter((f) => f.path.split('/').pop() === wantBase)
  if (baseHits.length === 1) return baseHits[0].path
  return null
}

/** Hide sandbox chart lines in tool JSON, step timeline, and old chat logs. */
function redactImagePayloadsInText(text) {
  if (!text || typeof text !== 'string') return text
  return text
    .split('\n')
    .map((line) => {
      const t = line.trim()
      if (/^(?:ADA|JARVIS)_IMAGE_(PNG|JPE?G|GIF|WEBP):/i.test(t)) return '[chart image hidden]'
      if (t.length > 200 && /^[A-Za-z0-9+/=]+$/.test(t) && t.startsWith('iVBOR')) return '[chart image hidden]'
      if (t.length > 200 && /^[A-Za-z0-9+/=]+$/.test(t) && t.startsWith('/9j')) return '[chart image hidden]'
      return line
    })
    .join('\n')
}

function formatToolCardResult(raw) {
  const s = String(raw ?? '')
  try {
    const o = JSON.parse(s)
    if (o && typeof o === 'object' && typeof o.stdout === 'string') {
      return JSON.stringify({ ...o, stdout: redactImagePayloadsInText(o.stdout) }, null, 2)
    }
    return JSON.stringify(o, null, 2)
  } catch {
    return redactImagePayloadsInText(s)
  }
}

const TOOL_PREVIEW_MAX_CHARS = 180

function truncateToolPreview(text, maxChars = TOOL_PREVIEW_MAX_CHARS) {
  const s = String(text)
  if (s.length <= maxChars) return s
  const cut = s.slice(0, maxChars)
  const lastNl = cut.lastIndexOf('\n')
  if (lastNl > 48) return `${cut.slice(0, lastNl).trimEnd()}…`
  const sp = cut.lastIndexOf(' ')
  return `${(sp > 56 ? cut.slice(0, sp) : cut).trimEnd()}…`
}

function ToolMessageCard({ content }) {
  const [expanded, setExpanded] = useState(false)
  try {
    const t = typeof content === 'string' ? JSON.parse(content) : content
    const name = t?.name || 'tool'
    const input = t?.input ?? ''
    const result = t?.result ?? ''
    const formatted = formatToolCardResult(result)
    const collapsible =
      name === 'web_search' ||
      (typeof formatted === 'string' && formatted.length > 1400)

    if (!collapsible) {
      return (
        <div className="msg-tool-card">
          <div className="msg-tool-title-row">
            <span className="msg-tool-label">
              🔧 {name}
              {input !== '' && input != null ? ` (${input})` : ''}
            </span>
          </div>
          <div className="msg-tool-result msg-tool-result--body">{formatted}</div>
        </div>
      )
    }

    const showFull = expanded
    const body = showFull ? formatted : truncateToolPreview(formatted)

    return (
      <div className={`msg-tool-card${showFull ? ' msg-tool-card--expanded' : ' msg-tool-card--collapsed'}`}>
        <div className="msg-tool-title-row">
          <span className="msg-tool-label msg-tool-label--grow" title={input ? String(input) : undefined}>
            🔧 {name}
            {input !== '' && input != null ? ` (${input})` : ''}
          </span>
          <button
            type="button"
            className="msg-tool-toggle"
            onClick={() => setExpanded((e) => !e)}
            aria-expanded={showFull}
          >
            {showFull ? 'Collapse' : 'View full'}
          </button>
        </div>
        <div className="msg-tool-result msg-tool-result--body">{body}</div>
      </div>
    )
  } catch {
    return <span className="msg-text">{content}</span>
  }
}

/** Copy assistant text without embedding multi‑MB base64 images. */
function stripChartDataUrlsForCopy(md) {
  if (!md || typeof md !== 'string') return md
  return md.replace(
    /!\[[^\]]*]\(data:image\/[^)]+\)/g,
    '![chart]([image omitted — see above])',
  )
}

const CHAT_ICON = (
  <svg className="chat-history-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
  </svg>
)

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

const COPY_ICON = (
  <svg className="msg-copy-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
)

const CLIP_MENU_ICON = (
  <svg className="chat-add-menu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <path d="M21.44 11.05l-9.19 9.19a6 6 0 01-8.49-8.49l9.19-9.19a4 4 0 015.66 5.66l-9.2 9.19a2 2 0 01-2.83-2.83l8.49-8.48" />
  </svg>
)

const GLOBE_MENU_ICON = (
  <svg className="chat-add-menu-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden>
    <circle cx="12" cy="12" r="10" />
    <path d="M2 12h20M12 2a15.3 15.3 0 014 10 15.3 15.3 0 01-4 10 15.3 15.3 0 01-4-10 15.3 15.3 0 014-10z" />
  </svg>
)

const NAV_NEW_CHAT_ICON = (
  <svg className="navbar-new-chat__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden>
    <path d="M12 5v14M5 12h14" />
  </svg>
)

function nextAgentRunLabel(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
  } catch {
    return ''
  }
}

/** Sidebar / footer folder mark (stroke, matches other UI icons). */
const REPO_FOLDER_ICON = (
  <svg className="repo-folder-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M3 7.5V19a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-7l-2-2H5a2 2 0 00-2 2v.5" />
  </svg>
)

function CopyResponseButton({ text }) {
  const [copied, setCopied] = useState(false)
  const plain = typeof text === 'string' ? text : String(text ?? '')
  const handleCopy = async () => {
    const toCopy = stripChartDataUrlsForCopy(plain)
    if (!toCopy.trim()) return
    try {
      await navigator.clipboard.writeText(toCopy)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = toCopy
        ta.setAttribute('readonly', '')
        ta.style.position = 'fixed'
        ta.style.left = '-9999px'
        document.body.appendChild(ta)
        ta.select()
        document.execCommand('copy')
        document.body.removeChild(ta)
        setCopied(true)
        window.setTimeout(() => setCopied(false), 2000)
      } catch {
        /* ignore */
      }
    }
  }
  return (
    <div className="msg-copy-row">
      <button
        type="button"
        className={`msg-copy-btn${copied ? ' msg-copy-btn--done' : ''}`}
        onClick={handleCopy}
        disabled={!stripChartDataUrlsForCopy(plain).trim()}
        aria-label={copied ? 'Copied to clipboard' : 'Copy response to clipboard'}
      >
        {COPY_ICON}
        <span>{copied ? 'Copied' : 'Copy'}</span>
      </button>
    </div>
  )
}

const RUNNABLE_RUNTIME = {
  '.py': 'Python',
  '.pyw': 'Python',
  '.js': 'Node',
  '.mjs': 'Node',
  '.cjs': 'Node',
  '.ts': 'TypeScript',
  '.mts': 'TypeScript',
  '.cts': 'TypeScript',
  '.ps1': 'PowerShell',
  '.sh': 'Bash',
  '.bash': 'Bash',
  '.rb': 'Ruby',
  '.go': 'Go',
  '.php': 'PHP',
  '.pl': 'Perl',
  '.lua': 'Lua',
  '.r': 'R',
  '.bat': 'Batch',
  '.cmd': 'Batch',
}

function runnableRuntimeForPath(relPath) {
  const name = String(relPath || '').replace(/\\/g, '/').split('/').pop() || ''
  const dot = name.lastIndexOf('.')
  if (dot < 0) return null
  return RUNNABLE_RUNTIME[name.slice(dot).toLowerCase()] || null
}

/** VS Code–style file tab above chat when opening from explorer (editable; Save writes disk or cache). */
function ChatFilePreview({ preview, onClose, onSave, onRun, colorScheme }) {
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [running, setRunning] = useState(false)
  const [saveError, setSaveError] = useState(null)
  const [saveFlash, setSaveFlash] = useState(null)
  const editorViewRef = useRef(null)
  const scrollCleanupRef = useRef(null)
  const saveHotkeyRef = useRef(() => {})
  const runHotkeyRef = useRef(() => {})
  const lineGutterRef = useRef(null)

  const lineCount = useMemo(() => Math.max(1, draft.split('\n').length), [draft])

  const syncLineGutterScroll = useCallback(() => {
    const view = editorViewRef.current
    const gh = lineGutterRef.current
    if (!view || !gh) return
    gh.scrollTop = view.scrollDOM.scrollTop
  }, [])

  useEffect(() => {
    syncLineGutterScroll()
  }, [draft, syncLineGutterScroll])

  useEffect(() => {
    setSaveError(null)
    setSaveFlash(null)
    setRunning(false)
    if (!preview) {
      setDraft('')
      return
    }
    if (preview.kind === 'image' || preview.loading || preview.error) {
      setDraft('')
      return
    }
    setDraft(preview.body ?? '')
  }, [
    preview?.relPath,
    preview?.loading,
    preview?.error,
    preview?.body,
    preview?.kind,
  ]) // eslint-disable-line react-hooks/exhaustive-deps -- resync when fields change, not preview reference identity

  const title = preview?.title ?? ''
  const loading = !!preview?.loading
  const error = preview?.error ?? null
  const source = preview?.source ?? null
  const body = preview?.body ?? ''
  const isImage = preview?.kind === 'image'
  const dirty = !!preview && !loading && !error && !isImage && draft !== body
  const showEditor = !!preview && !loading && !error && !isImage

  const handleSave = useCallback(async () => {
    if (!onSave || !preview?.relPath || saving || !dirty) return { ok: true, skipped: true }
    setSaving(true)
    setSaveError(null)
    setSaveFlash(null)
    try {
      const r = await onSave(preview.relPath, draft)
      if (!r?.ok) {
        setSaveError(r?.error || 'Save failed.')
        return r || { ok: false, error: 'Save failed.' }
      }
      setSaveFlash(
        r.cacheOnly ? 'Saved to browser cache (re-link folder to write disk).' : 'Saved to disk.',
      )
      window.setTimeout(() => setSaveFlash(null), 4000)
      return r
    } catch (e) {
      const msg = e?.message || 'Save failed.'
      setSaveError(msg)
      return { ok: false, error: msg }
    } finally {
      setSaving(false)
    }
  }, [onSave, preview?.relPath, saving, dirty, draft])

  const runtimeLabel = runnableRuntimeForPath(preview?.relPath)
  const canRun = !!onRun && !!preview?.relPath && !!runtimeLabel && showEditor

  const handleRun = useCallback(async () => {
    if (!onRun || !preview?.relPath || running || saving) return
    if (!runnableRuntimeForPath(preview.relPath)) {
      setSaveError('No Run command for this file type.')
      return
    }
    if (dirty) {
      if (!onSave) {
        setSaveError('Save the file first, then Run.')
        return
      }
      const saved = await handleSave()
      if (!saved?.ok) return
      if (saved.cacheOnly) {
        setSaveError('This file is only in the browser cache. Link the folder so Run can execute it on disk.')
        return
      }
    }
    setRunning(true)
    setSaveError(null)
    setSaveFlash(null)
    try {
      const r = await onRun(preview.relPath)
      if (!r?.ok) {
        setSaveError(r?.error || 'Run failed.')
        return
      }
      setSaveFlash(`Finished (exit ${r.returncode ?? 0}).`)
      window.setTimeout(() => setSaveFlash(null), 4000)
    } catch (e) {
      setSaveError(e?.message || 'Run failed.')
    } finally {
      setRunning(false)
    }
  }, [onRun, preview?.relPath, running, saving, dirty, onSave, handleSave])

  useEffect(() => {
    saveHotkeyRef.current = () => {
      void handleSave()
    }
  }, [handleSave])

  useEffect(() => {
    runHotkeyRef.current = () => {
      void handleRun()
    }
  }, [handleRun])

  useEffect(() => {
    if (!canRun) return undefined
    const onKey = (e) => {
      if (e.key !== 'F5') return
      e.preventDefault()
      runHotkeyRef.current()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [canRun])

  const scheme = colorScheme === 'light' ? 'light' : 'dark'
  const cmExtensions = useMemo(
    () => [
      ...themeExtensionsForScheme(scheme),
      ...languageExtensionsForPath(preview?.relPath ?? ''),
      Prec.highest(
        keymap.of([
          {
            key: 'Mod-s',
            run: () => {
              saveHotkeyRef.current()
              return true
            },
          },
          {
            key: 'F5',
            run: () => {
              runHotkeyRef.current()
              return true
            },
          },
          {
            key: 'Mod-F5',
            run: () => {
              runHotkeyRef.current()
              return true
            },
          },
        ]),
      ),
    ],
    [scheme, preview?.relPath],
  )

  const onCreateEditor = useCallback((view) => {
    editorViewRef.current = view
    scrollCleanupRef.current?.()
    const el = view.scrollDOM
    const sync = () => {
      const gh = lineGutterRef.current
      if (gh) gh.scrollTop = el.scrollTop
    }
    el.addEventListener('scroll', sync, { passive: true })
    sync()
    scrollCleanupRef.current = () => el.removeEventListener('scroll', sync)
  }, [])

  useEffect(() => {
    return () => {
      scrollCleanupRef.current?.()
      scrollCleanupRef.current = null
      editorViewRef.current = null
    }
  }, [preview?.relPath])

  if (!preview) return null

  return (
    <div className="chat-file-preview" role="region" aria-label="Open file">
      <div className="chat-file-preview__toolbar">
        <span className="chat-file-preview__path" title={title}>
          {title}
        </span>
        {showEditor && (onSave || canRun) ? (
          <div className="chat-file-preview__actions">
            {saveError ? <span className="chat-file-preview__save-msg chat-file-preview__save-msg--err">{saveError}</span> : null}
            {saveFlash && !saveError ? (
              <span className="chat-file-preview__save-msg chat-file-preview__save-msg--ok">{saveFlash}</span>
            ) : null}
            {onSave ? (
              <button
                type="button"
                className="chat-file-preview__save"
                onClick={() => handleSave()}
                disabled={!dirty || saving || running}
                title="Save (Ctrl+S)"
              >
                {saving ? 'Saving…' : 'Save'}
              </button>
            ) : null}
            {canRun ? (
              <button
                type="button"
                className="chat-file-preview__run"
                onClick={() => handleRun()}
                disabled={saving || running}
                title={`Run ${runtimeLabel} File (F5)`}
              >
                <span aria-hidden>▶</span>
                {running ? 'Running…' : 'Run'}
              </button>
            ) : null}
          </div>
        ) : null}
        <button type="button" className="chat-file-preview__close" onClick={onClose} aria-label="Close file">
          ×
        </button>
      </div>
      {source === 'snapshot' && !isImage ? (
        <p className="chat-file-preview__hint">
          Showing text from your project index (may be truncated). Re-open the folder with the system folder picker for
          full file access.
        </p>
      ) : null}
      {source === 'cache' ? (
        <p className="chat-file-preview__hint">
          {isImage
            ? 'No live folder handle — image from browser cache. Use &quot;Choose folder&quot; again for the latest from disk.'
            : 'No live folder handle — edits save to the browser cache only until you use &quot;Choose folder&quot; again.'}
        </p>
      ) : null}
      {loading ? <div className="chat-file-preview__loading">Loading…</div> : null}
      {error ? <div className="chat-file-preview__error">{error}</div> : null}
      {showEditor ? (
        <div className="chat-file-preview__body">
          <div className="chat-file-preview__editor-wrap">
            <CodeMirror
              className="chat-file-preview__codemirror"
              value={draft}
              height="100%"
              minHeight="10rem"
              theme="none"
              indentWithTab
              basicSetup={{ lineNumbers: false, foldGutter: false }}
              extensions={cmExtensions}
              onCreateEditor={onCreateEditor}
              onChange={(v) => setDraft(v)}
              aria-label="File contents"
            />
            <div
              className="chat-file-preview__line-gutter"
              style={{ width: `${Math.max(2, String(lineCount).length) + 1}ch` }}
              aria-hidden
            >
              <div ref={lineGutterRef} className="chat-file-preview__line-gutter-scroll">
                {Array.from({ length: lineCount }, (_, i) => (
                  <div key={i + 1} className="chat-file-preview__line-num">
                    {i + 1}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      ) : null}
      {isImage && !loading && !error && preview.imageUrl ? (
        <div className="chat-file-preview__body chat-file-preview__body--media">
          <img src={preview.imageUrl} alt="" className="chat-file-preview__image" />
        </div>
      ) : null}
    </div>
  )
}

const CODING_MODE_KEY = 'jarvis-coding-mode-enabled'

function readStoredCodingMode() {
  try {
    const v = localStorage.getItem(CODING_MODE_KEY) ?? localStorage.getItem('ada-coding-mode-enabled')
    if (v === '0') return false
    if (v === '1') return true
  } catch {
    /* ignore */
  }
  return true
}

const COLOR_SCHEME_KEY = 'jarvis-color-scheme'

function readStoredColorScheme() {
  try {
    const v = localStorage.getItem(COLOR_SCHEME_KEY) ?? localStorage.getItem('ada-color-scheme')
    if (v === 'light' || v === 'dark') return v
  } catch {
    /* ignore */
  }
  return 'dark'
}

const TERMINAL_EXPANDED_KEY = 'jarvis-terminal-expanded'

/** VS Code–style host shell strip: one-line collapsed bar; expand for output + single-line input (uses POST /tools/shell). */
const ChatTerminalPanel = forwardRef(function ChatTerminalPanel(_props, ref) {
  const [expanded, setExpanded] = useState(() => {
    try {
      return (localStorage.getItem(TERMINAL_EXPANDED_KEY) ?? localStorage.getItem('ada-terminal-expanded')) === '1'
    } catch {
      return false
    }
  })
  const setExpandedPersist = useCallback((v) => {
    setExpanded(v)
    try {
      if (v) localStorage.setItem(TERMINAL_EXPANDED_KEY, '1')
      else localStorage.removeItem(TERMINAL_EXPANDED_KEY)
    } catch {
      /* ignore */
    }
  }, [])

  const [lines, setLines] = useState([])
  const [cmd, setCmd] = useState('')
  const [busy, setBusy] = useState(false)
  const [shellLabel, setShellLabel] = useState(null)
  const outRef = useRef(null)

  useEffect(() => {
    if (!expanded) return
    const el = outRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [lines, expanded])

  const appendResult = useCallback((result) => {
    if (result?.runtime) setShellLabel(result.runtime)
    else if (result?.shell) setShellLabel(result.shell)
    if (!result?.ok) {
      const parts = [result?.error, result?.stderr].filter(Boolean)
      const msg = parts.join('\n') || `Exited with code ${result?.returncode ?? -1}.`
      setLines((L) => [...L, { kind: 'err', text: msg }].slice(-400))
      return
    }
    let out = ''
    if (result?.stdout) out += result.stdout
    if (result?.stderr) out += (out ? '\n' : '') + result.stderr
    setLines((L) => [...L, { kind: 'out', text: out || `(exit ${result?.returncode ?? 0})` }].slice(-400))
  }, [])

  useImperativeHandle(
    ref,
    () => ({
      prepareRun(command) {
        setExpandedPersist(true)
        setBusy(true)
        const label = command || 'Run'
        setLines((L) => [...L, { kind: 'in', text: label }].slice(-400))
      },
      finishRun(result) {
        setBusy(false)
        if (result?.display) {
          setLines((L) => {
            const next = L.slice()
            for (let i = next.length - 1; i >= 0; i -= 1) {
              if (next[i].kind === 'in') {
                next[i] = { ...next[i], text: result.display }
                break
              }
            }
            return next.slice(-400)
          })
        }
        appendResult(result || { ok: false, error: 'Run failed.' })
      },
    }),
    [appendResult, setExpandedPersist],
  )

  const run = useCallback(async () => {
    const c = cmd.trim()
    if (!c || busy) return
    setCmd('')
    setLines((L) => [...L, { kind: 'in', text: c }].slice(-400))
    setBusy(true)
    try {
      const r = await runHostShellCommand(c, 120)
      if (r.shell) setShellLabel(r.shell)
      if (!r.ok) {
        const parts = [r.error, r.stderr].filter(Boolean)
        const msg = parts.join('\n') || `Exited with code ${r.returncode ?? -1}.`
        setLines((L) => [...L, { kind: 'err', text: msg }].slice(-400))
      } else {
        let out = ''
        if (r.stdout) out += r.stdout
        if (r.stderr) out += (out ? '\n' : '') + r.stderr
        setLines((L) => [...L, { kind: 'out', text: out || `(exit ${r.returncode})` }].slice(-400))
      }
    } catch (e) {
      setLines((L) => [...L, { kind: 'err', text: e?.message || String(e) }].slice(-400))
    } finally {
      setBusy(false)
    }
  }, [cmd, busy])

  const shellSummary = shellLabel
    ? shellLabel === 'powershell'
      ? 'PowerShell'
      : shellLabel === 'bash'
        ? 'Bash'
        : shellLabel
    : 'Server shell'

  if (!expanded) {
    return (
      <div className="chat-terminal chat-terminal--collapsed">
        <button
          type="button"
          className="chat-terminal__bar"
          onClick={() => setExpandedPersist(true)}
          aria-expanded="false"
        >
          <span className="chat-terminal__chev" aria-hidden>
            ▲
          </span>
          <span className="chat-terminal__label">TERMINAL</span>
          <span className="chat-terminal__hint">
            {busy ? 'Running…' : `${shellSummary} — Run (F5) or expand`}
          </span>
        </button>
      </div>
    )
  }

  return (
    <div className="chat-terminal chat-terminal--expanded">
      <div className="chat-terminal__head">
        <button
          type="button"
          className="chat-terminal__collapse"
          onClick={() => setExpandedPersist(false)}
          aria-expanded="true"
          aria-label="Collapse terminal panel"
        >
          ▼
        </button>
        <span className="chat-terminal__title">TERMINAL</span>
        <span className="chat-terminal__meta">{shellSummary}</span>
        <button type="button" className="chat-terminal__clear" onClick={() => setLines([])}>
          Clear
        </button>
      </div>
      <div className="chat-terminal__out-wrap" ref={outRef}>
        {lines.length === 0 ? (
          <div className="chat-terminal__placeholder">
            Run the open file with ▶ Run or F5. Or type one host command per line. File Run uses the linked project
            folder and does not require the host shell.
          </div>
        ) : (
          lines.map((row, i) => (
            <div key={i} className={`chat-terminal__line chat-terminal__line--${row.kind}`}>
              {row.kind === 'in' ? <span className="chat-terminal__tag">$</span> : null}
              <pre className="chat-terminal__pre">{row.text}</pre>
            </div>
          ))
        )}
      </div>
      <div className="chat-terminal__input-row">
        <span className="chat-terminal__prompt" aria-hidden>
          &gt;
        </span>
        <input
          type="text"
          className="chat-terminal__input"
          value={cmd}
          onChange={(e) => setCmd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              run()
            }
          }}
          placeholder={busy ? 'Running…' : 'Command (Enter)'}
          disabled={busy}
          spellCheck={false}
          autoComplete="off"
          aria-label="Terminal command"
        />
      </div>
    </div>
  )
})

function App() {
  const [colorScheme, setColorScheme] = useState(readStoredColorScheme)
  const [panel, setPanel] = useState('chats')
  const [chats, setChats] = useState([])
  const [currentChatId, setCurrentChatIdState] = useState(null)
  const [chatMeta, setChatMeta] = useState(null)
  const currentChatIdRef = useRef(null)
  const loopPollRef = useRef(null)
  const terminalRef = useRef(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState([])
  const [storagePath, setStoragePath] = useState('')
  const [modelProvider, setModelProvider] = useState('openai')
  const [localModelId, setLocalModelId] = useState('')
  const [localModelsInfo, setLocalModelsInfo] = useState(null)
  const [localJob, setLocalJob] = useState(null)
  const [googleAuth, setGoogleAuth] = useState({
    configured: false,
    connected: false,
    user: null,
  })
  const [gmailStatus, setGmailStatus] = useState(null)
  const [googleAuthBusy, setGoogleAuthBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const [liveReply, setLiveReply] = useState(null)
  const [streamTimeline, setStreamTimeline] = useState([])
  const [livePlan, setLivePlan] = useState([])
  /** Screenshots arrive on WebSocket; SSE step may arrive first or second — stash by step id */
  const screenshotPendingRef = useRef({})
  const wsRef = useRef(null)
  const streamJarvisStripRef = useRef('')
  const messagesEndRef = useRef(null)
  const fileInputRef = useRef(null)
  const projectFolderInputRef = useRef(null)
  const projectRootHandleRef = useRef(null)
  const addMenuRef = useRef(null)
  const chatInputRef = useRef(null)
  const mentionUiRef = useRef(null)
  const [addMenuOpen, setAddMenuOpen] = useState(false)
  const [fileMention, setFileMention] = useState(null)
  const [slashMention, setSlashMention] = useState(null)
  const [slashCatalog, setSlashCatalog] = useState([])
  const slashUiRef = useRef(null)
  const workspaceClearedRef = useRef(false)
  const [webSearchMode, setWebSearchMode] = useState(() => {
    try {
      return sessionStorage.getItem('jarvis-web-search-mode') === '1' || sessionStorage.getItem('ada-web-search-mode') === '1'
    } catch {
      return false
    }
  })
  const [workspaceLocalLabel, setWorkspaceLocalLabel] = useState(() => {
    try {
      return sessionStorage.getItem('jarvis-workspace-local-label') || sessionStorage.getItem('ada-workspace-local-label') || ''
    } catch {
      return ''
    }
  })
  const [workspaceSnapshot, setWorkspaceSnapshot] = useState(() => {
    try {
      return sessionStorage.getItem('jarvis-workspace-snapshot') || sessionStorage.getItem('ada-workspace-snapshot') || ''
    } catch {
      return ''
    }
  })
  const [workspaceRelPaths, setWorkspaceRelPaths] = useState(() => {
    try {
      const raw = sessionStorage.getItem('jarvis-workspace-paths') || sessionStorage.getItem('ada-workspace-paths')
      const parsed = raw ? JSON.parse(raw) : null
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  })
  const [projectImportBusy, setProjectImportBusy] = useState(false)
  const [codingModeEnabled, setCodingModeEnabled] = useState(readStoredCodingMode)
  const [filePreview, setFilePreview] = useState(null)
  /** Pending ```jarvis-file``` edits from last assistant turn (review before apply). */
  const [pendingWorkspaceEdits, setPendingWorkspaceEdits] = useState(null)
  const [workspaceReviewFileIndex, setWorkspaceReviewFileIndex] = useState(0)
  /** Paths removed from the review queue (kept/discarded) so the right-rail cards stay in sync. */
  const [workspaceReviewHiddenPaths, setWorkspaceReviewHiddenPaths] = useState(() => new Set())
  const [workspaceUndoTick, setWorkspaceUndoTick] = useState(0)
  const [workspaceUndoUi, setWorkspaceUndoUi] = useState({ canUndo: false, canRedo: false })
  const [introduceWizard, setIntroduceWizard] = useState(null)
  const introduceWizardRef = useRef(null)
  const [pendingApprovals, setPendingApprovals] = useState([])
  const [autonomyLevel, setAutonomyLevelState] = useState('gated')
  const [runMode, setRunModeState] = useState('agent')
  const [spendLimits, setSpendLimitsState] = useState({ max_tokens_per_run: 80000, warn_tokens: 40000 })
  const [quietHours, setQuietHoursState] = useState({
    enabled: false,
    start: '23:00',
    end: '08:00',
    timezone: 'local',
  })
  const [controlTasks, setControlTasks] = useState([])
  const [activeRuns, setActiveRuns] = useState([])
  const [liveUsage, setLiveUsage] = useState(null)
  const [checkpoints, setCheckpoints] = useState([])
  const [facts, setFacts] = useState([])
  const [factDraft, setFactDraft] = useState('')
  const [identity, setIdentity] = useState({ soul: '', user: '', memory: '' })
  const [desktopArmed, setDesktopArmedState] = useState(false)
  const [keysStatus, setKeysStatus] = useState({ openai_set: false, xai_set: false })
  const [openaiKeyDraft, setOpenaiKeyDraft] = useState('')
  const [xaiKeyDraft, setXaiKeyDraft] = useState('')
  const [workspaceDiskPath, setWorkspaceDiskPath] = useState('')
  const [messageQueue, setMessageQueue] = useState([])
  const [queueHeld, setQueueHeld] = useState(false)
  const [chatSearchQ, setChatSearchQ] = useState('')
  const [chatSearchHits, setChatSearchHits] = useState([])
  const chatSearchTimerRef = useRef(null)
  const [bookmarks, setBookmarks] = useState([])
  const [messageVotes, setMessageVotes] = useState({})
  const [reactionBusyKey, setReactionBusyKey] = useState('')
  const [usageStats, setUsageStats] = useState(null)
  const [customAgents, setCustomAgents] = useState([])
  const [activeAgentId, setActiveAgentId] = useState(null)
  const [agentEditorId, setAgentEditorId] = useState(null)
  const [creatingAgent, setCreatingAgent] = useState(false)
  const [createAgentOpen, setCreateAgentOpen] = useState(false)
  const [createAgentError, setCreateAgentError] = useState('')
  const abortRef = useRef(null)
  const workspaceTreeStampRef = useRef('')
  const lastRunIdRef = useRef(null)
  const activeRunsRef = useRef([])
  const sendingRef = useRef(false)
  const messageQueueRef = useRef([])
  const queueHeldRef = useRef(false)

  useEffect(() => {
    introduceWizardRef.current = introduceWizard
  }, [introduceWizard])

  useEffect(() => {
    currentChatIdRef.current = currentChatId
  }, [currentChatId])

  useEffect(() => {
    return () => {
      if (loopPollRef.current) clearInterval(loopPollRef.current)
      if (chatSearchTimerRef.current) clearTimeout(chatSearchTimerRef.current)
    }
  }, [])

  const [codingLayoutWidths, setCodingLayoutWidths] = useState(() => readCodingLayoutWidths())

  useEffect(() => {
    try {
      localStorage.setItem(CODING_LAYOUT_STORAGE_KEY, JSON.stringify(codingLayoutWidths))
    } catch {
      /* ignore */
    }
  }, [codingLayoutWidths])

  const onExplorerPanelResizePointerDown = useCallback(
    (e) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.stopPropagation()
      const startX = e.clientX
      const startW = codingLayoutWidths.explorer
      const onMove = (ev) => {
        const dx = ev.clientX - startX
        const next = clampPanelWidth(startW + dx, EXPLORER_PANEL.min, EXPLORER_PANEL.max)
        setCodingLayoutWidths((prev) => ({ ...prev, explorer: next }))
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        document.body.style.removeProperty('cursor')
        document.body.style.removeProperty('user-select')
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
    },
    [codingLayoutWidths.explorer],
  )

  const onChatRailResizePointerDown = useCallback(
    (e) => {
      if (e.button !== 0) return
      e.preventDefault()
      e.stopPropagation()
      const startX = e.clientX
      const startW = codingLayoutWidths.chatRail
      const onMove = (ev) => {
        const dx = startX - ev.clientX
        const next = clampPanelWidth(startW + dx, CHAT_RAIL_PANEL.min, CHAT_RAIL_PANEL.max)
        setCodingLayoutWidths((prev) => ({ ...prev, chatRail: next }))
      }
      const onUp = () => {
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
        window.removeEventListener('pointercancel', onUp)
        document.body.style.removeProperty('cursor')
        document.body.style.removeProperty('user-select')
      }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
      window.addEventListener('pointercancel', onUp)
    },
    [codingLayoutWidths.chatRail],
  )

  const nudgeExplorerPanel = useCallback((delta) => {
    setCodingLayoutWidths((prev) => ({
      ...prev,
      explorer: clampPanelWidth(prev.explorer + delta, EXPLORER_PANEL.min, EXPLORER_PANEL.max),
    }))
  }, [])

  const nudgeChatRailPanel = useCallback((delta) => {
    setCodingLayoutWidths((prev) => ({
      ...prev,
      chatRail: clampPanelWidth(prev.chatRail + delta, CHAT_RAIL_PANEL.min, CHAT_RAIL_PANEL.max),
    }))
  }, [])

  useEffect(() => {
    mentionUiRef.current = fileMention
  }, [fileMention])

  useEffect(() => {
    slashUiRef.current = slashMention
  }, [slashMention])

  useEffect(() => {
    let cancelled = false
    getSlashCatalog()
      .then((data) => {
        if (cancelled) return
        const rows = [
          ...(data?.builtins || []),
          ...(data?.commands || []),
          ...(data?.skills || []).map((s) => ({ ...s, skill: true })),
        ]
        const seen = new Set()
        const uniq = []
        for (const row of rows) {
          const key = String(row?.name || '').toLowerCase()
          if (!key || seen.has(key)) continue
          seen.add(key)
          uniq.push(row)
        }
        setSlashCatalog(uniq)
      })
      .catch(() => {
        if (!cancelled) setSlashCatalog([])
      })
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!codingModeEnabled || workspaceRelPaths.length === 0) setFileMention(null)
  }, [codingModeEnabled, workspaceRelPaths.length])

  useEffect(() => {
    setWorkspaceReviewFileIndex(0)
    setWorkspaceReviewHiddenPaths(new Set())
  }, [pendingWorkspaceEdits?.id])

  const visibleWorkspaceEditsSession = useMemo(() => {
    if (!pendingWorkspaceEdits?.files?.length) return null
    const files = pendingWorkspaceEdits.files.filter((f) => !workspaceReviewHiddenPaths.has(f.path))
    if (!files.length) return null
    return { ...pendingWorkspaceEdits, files }
  }, [pendingWorkspaceEdits, workspaceReviewHiddenPaths])

  useEffect(() => {
    const n = visibleWorkspaceEditsSession?.files?.length ?? 0
    if (n === 0) return
    setWorkspaceReviewFileIndex((i) => Math.min(Math.max(0, i), n - 1))
  }, [visibleWorkspaceEditsSession?.files?.length])

  const selectWorkspaceEditFile = useCallback(
    (pathOrLabel) => {
      const files = visibleWorkspaceEditsSession?.files
      if (!files?.length || pathOrLabel == null) return
      const raw = String(pathOrLabel).trim()
      const path = files.some((f) => f.path === raw)
        ? raw
        : resolveEditFilePathFromHeading(files, raw)
      if (!path) return
      const i = files.findIndex((f) => f.path === path)
      if (i < 0) return
      setWorkspaceReviewFileIndex(i)
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          document
            .querySelector(
              '.coding-workbench .workspace-file-review--workbench[aria-label="Workspace file changes"]'
            )
            ?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        })
      })
    },
    [visibleWorkspaceEditsSession]
  )

  const workspaceEditMarkdownComponents = useMemo(() => {
    const files = visibleWorkspaceEditsSession?.files
    const pre = (props) => {
      const child = Array.isArray(props.children) ? props.children[0] : props.children
      const className = child?.props?.className || ''
      const lang = (className.match(/language-([\w-]+)/) || [, ''])[1]
      if (isPreviewLanguage(lang)) {
        return (
          <LivePreview language={lang} className={className}>
            {markdownNodeToPlainText(child?.props?.children)}
          </LivePreview>
        )
      }
      return <pre {...props}>{props.children}</pre>
    }
    if (!files?.length) return { pre }
    const mk = (Tag) =>
      function WorkspaceEditHeading(props) {
        const plain = markdownNodeToPlainText(props.children)
        const path = resolveEditFilePathFromHeading(files, plain)
        if (!path) return <Tag {...props} />
        return (
          <Tag {...props}>
            <button
              type="button"
              className="msg-md-file-jump"
              title={path}
              onClick={(e) => {
                e.preventDefault()
                e.stopPropagation()
                selectWorkspaceEditFile(path)
              }}
            >
              {props.children}
            </button>
          </Tag>
        )
      }
    return {
      pre,
      h1: mk('h1'),
      h2: mk('h2'),
      h3: mk('h3'),
      h4: mk('h4'),
    }
  }, [visibleWorkspaceEditsSession, selectWorkspaceEditFile])

  const syncSlashMentionFromCaret = useCallback(
    (value, cursorPos) => {
      const before = String(value || '').slice(0, cursorPos ?? 0)
      const m = before.match(/(?:^|\n)\/([a-zA-Z][\w:-]*)?$/)
      if (!m) {
        setSlashMention(null)
        return
      }
      const query = (m[1] || '').toLowerCase()
      const start = before.lastIndexOf('/')
      const matches = (slashCatalog.length ? slashCatalog : [{ name: 'help', description: 'Show the chat manual' }])
        .filter((row) => String(row.name || '').toLowerCase().startsWith(query))
        .slice(0, 12)
      setSlashMention((prev) => ({
        start,
        query,
        matches,
        highlight:
          prev && prev.query === query
            ? Math.min(prev.highlight || 0, Math.max(0, matches.length - 1))
            : 0,
      }))
    },
    [slashCatalog],
  )

  const applySlashMention = useCallback((name) => {
    const m = slashUiRef.current
    const el = chatInputRef.current
    if (!m || !el || !name) return
    const v = el.value
    const cur = el.selectionStart ?? v.length
    const before = v.slice(0, m.start)
    const after = v.slice(cur)
    const insertion = `/${name} `
    const next = before + insertion + after
    const caret = before.length + insertion.length
    setInput(next)
    setSlashMention(null)
    setTimeout(() => {
      el.focus()
      el.setSelectionRange(caret, caret)
    }, 0)
  }, [])

  const syncFileMentionFromCaret = useCallback(
    (value, cursorPos, kind) => {
      syncSlashMentionFromCaret(value, cursorPos)
      if (!codingModeEnabled || workspaceRelPaths.length === 0) {
        setFileMention(null)
        return
      }
      const m = getActiveFileMention(value, cursorPos)
      if (!m) {
        setFileMention(null)
        return
      }
      const matches = rankProjectPathMatches(workspaceRelPaths, m.query, 14)
      setFileMention((prev) => {
        let highlight = 0
        if (
          kind === 'select' &&
          prev &&
          prev.start === m.start &&
          prev.query === m.query &&
          prev.matches.length === matches.length &&
          prev.matches.every((p, i) => p === matches[i])
        ) {
          highlight = Math.min(prev.highlight, Math.max(0, matches.length - 1))
        }
        const prevSel = prev?.selectedPaths || []
        const selectedPaths = prevSel.filter((p) => matches.includes(p))
        return {
          start: m.start,
          query: m.query,
          matches,
          highlight,
          selectedPaths,
        }
      })
    },
    [codingModeEnabled, workspaceRelPaths, syncSlashMentionFromCaret],
  )

  const bumpFileMentionHighlight = useCallback((delta) => {
    setFileMention((mu) => {
      if (!mu?.matches?.length) return mu
      const n = mu.matches.length
      let h = (mu.highlight + delta) % n
      if (h < 0) h += n
      return { ...mu, highlight: h }
    })
  }, [])

  const toggleFileMentionSelect = useCallback((relPath) => {
    setFileMention((fm) => {
      if (!fm?.matches?.includes(relPath)) return fm
      const cur = [...(fm.selectedPaths || [])]
      const i = cur.indexOf(relPath)
      if (i >= 0) cur.splice(i, 1)
      else cur.push(relPath)
      return { ...fm, selectedPaths: cur }
    })
  }, [])

  const applyFileMentionMany = useCallback((paths) => {
    const m = mentionUiRef.current
    const el = chatInputRef.current
    if (!m || !el) return
    const unique = [...new Set(paths)].filter(Boolean)
    if (!unique.length) return
    const v = el.value
    const cur = el.selectionStart ?? v.length
    const before = v.slice(0, m.start)
    const after = v.slice(cur)
    const insertion = `${unique.map((p) => `@${p}`).join(' ')} `
    const next = before + insertion + after
    const caret = before.length + insertion.length
    setInput(next)
    setFileMention(null)
    setTimeout(() => {
      try {
        el.focus()
        el.setSelectionRange(caret, caret)
      } catch {
        /* ignore */
      }
    }, 0)
  }, [])

  const applyFileMention = useCallback((relPath) => {
    applyFileMentionMany([relPath])
  }, [applyFileMentionMany])

  const refreshChatList = useCallback(async () => {
    try {
      const list = await listChats()
      setChats(list)
    } catch {
      setChats([])
    }
  }, [])

  const refreshCustomAgents = useCallback(async () => {
    try {
      const data = await listCustomAgents(true)
      setCustomAgents(Array.isArray(data?.agents) ? data.agents : [])
    } catch {
      setCustomAgents([])
    }
  }, [])

  const agentChatIds = useMemo(
    () => new Set((customAgents || []).map((a) => a.chat_id).filter(Boolean)),
    [customAgents],
  )
  const generalChats = useMemo(
    () => (chats || []).filter((c) => !agentChatIds.has(c.id)),
    [chats, agentChatIds],
  )
  const activeAgent = useMemo(
    () => (customAgents || []).find((a) => a.id === activeAgentId) || null,
    [customAgents, activeAgentId],
  )
  const visibleAgents = useMemo(
    () => (customAgents || []).filter((a) => !a.hidden),
    [customAgents],
  )
  const hiddenAgents = useMemo(
    () => (customAgents || []).filter((a) => a.hidden),
    [customAgents],
  )

  const refreshStoragePath = useCallback(async () => {
    try {
      const path = await getChatsStoragePath()
      setStoragePath(path || '')
    } catch {
      setStoragePath('')
    }
  }, [])

  const refreshModelSetting = useCallback(async () => {
    try {
      const data = await getModelSetting()
      setModelProvider(data?.provider || 'openai')
      setLocalModelId(data?.local_model_id || '')
    } catch {
      setModelProvider('openai')
    }
  }, [])

  const refreshLocalModels = useCallback(async () => {
    try {
      const data = await getLocalModels()
      setLocalModelsInfo(data)
      if (data?.job) setLocalJob(data.job)
    } catch {
      setLocalModelsInfo(null)
    }
  }, [])

  const refreshGoogleAuth = useCallback(async () => {
    try {
      const status = await getGoogleAuthStatus()
      const connected = !!status?.connected
      setGoogleAuth({
        configured: !!status?.configured,
        connected,
        user: status?.user || null,
      })
      if (connected) {
        try {
          const g = await getGmailProfile()
          if (g?.ok) {
            setGmailStatus({
              ok: true,
              emailAddress: g.emailAddress,
              messagesTotal: g.messagesTotal,
            })
          } else {
            setGmailStatus({ ok: false, error: g?.error || 'Gmail unavailable' })
          }
        } catch (e) {
          setGmailStatus({ ok: false, error: e?.message || 'Gmail check failed' })
        }
      } else {
        setGmailStatus(null)
      }
    } catch {
      setGoogleAuth({ configured: false, connected: false, user: null })
      setGmailStatus(null)
    }
  }, [])

  const refreshRuntime = useCallback(async () => {
    try {
      const rt = await getRuntimeSettings()
      if (rt?.autonomy) setAutonomyLevelState(rt.autonomy)
      if (rt?.run_mode) setRunModeState(rt.run_mode)
      if (rt?.spend) setSpendLimitsState(rt.spend)
      if (rt?.quiet_hours) setQuietHoursState(rt.quiet_hours)
      setDesktopArmedState(!!rt?.desktop_armed)
      if (rt?.keys) setKeysStatus(rt.keys)
    } catch {
      /* ignore */
    }
    try {
      const ws = await getWorkspaceStatus()
      setWorkspaceDiskPath(ws?.linked ? ws.path || '' : '')
      if (ws?.linked && !workspaceClearedRef.current) {
        const listed = await listWorkspaceFiles()
        if (listed?.ok && Array.isArray(listed.paths) && listed.paths.length && !workspaceClearedRef.current) {
          setWorkspaceRelPaths(listed.paths)
          try {
            sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(listed.paths))
          } catch {
            /* ignore */
          }
          if (ws.label) {
            setWorkspaceLocalLabel((prev) => prev || ws.label)
            try {
              if (!sessionStorage.getItem('jarvis-workspace-local-label')) {
                sessionStorage.setItem('jarvis-workspace-local-label', ws.label)
              }
            } catch {
              /* ignore */
            }
          }
        }
      }
    } catch {
      /* ignore */
    }
  }, [])

  const refreshBookmarks = useCallback(async () => {
    try {
      const data = await listBookmarks()
      setBookmarks(Array.isArray(data?.bookmarks) ? data.bookmarks : [])
    } catch {
      setBookmarks([])
    }
  }, [])

  const refreshControlPlane = useCallback(async () => {
    try {
      const data = await listTasks(null, true)
      setControlTasks(Array.isArray(data?.tasks) ? data.tasks : [])
    } catch {
      setControlTasks([])
    }
    try {
      const data = await listActiveRuns()
      setActiveRuns(Array.isArray(data?.runs) ? data.runs : [])
    } catch {
      setActiveRuns([])
    }
    try {
      const data = await listCheckpoints()
      setCheckpoints(Array.isArray(data?.checkpoints) ? data.checkpoints : [])
    } catch {
      setCheckpoints([])
    }
  }, [])

  const refreshMemoryExtras = useCallback(async () => {
    try {
      const data = await listFacts()
      setFacts(Array.isArray(data?.facts) ? data.facts : [])
    } catch {
      setFacts([])
    }
    try {
      setIdentity(await getIdentity())
    } catch {
      /* ignore */
    }
  }, [])

  const refreshUsage = useCallback(async () => {
    try {
      setUsageStats(await getUsageStats())
    } catch {
      /* ignore */
    }
  }, [])

  const refreshPendingApprovals = useCallback(async () => {
    try {
      const data = await listPendingApprovals(currentChatId)
      setPendingApprovals(Array.isArray(data?.pending) ? data.pending : [])
    } catch {
      setPendingApprovals([])
    }
  }, [currentChatId])

  const refreshSettings = useCallback(async () => {
    await refreshStoragePath()
    await refreshModelSetting()
    await refreshGoogleAuth()
    await refreshRuntime()
    await refreshLocalModels()
    await refreshControlPlane()
    await refreshMemoryExtras()
  }, [refreshStoragePath, refreshModelSetting, refreshGoogleAuth, refreshRuntime, refreshLocalModels, refreshControlPlane, refreshMemoryExtras])

  useEffect(() => {
    activeRunsRef.current = activeRuns
  }, [activeRuns])

  const stopEverything = useCallback(async () => {
    abortRef.current?.abort()
    const ids = new Set()
    if (lastRunIdRef.current) ids.add(lastRunIdRef.current)
    for (const r of activeRunsRef.current || []) {
      const id = r?.run_id || r?.id
      if (id) ids.add(id)
    }
    await Promise.all([...ids].map((id) => stopActiveRun(id).catch(() => {})))
  }, [])

  const selectChat = useCallback(async (chatId) => {
    try {
      await stopEverything()
      await setCurrentChat(chatId)
      setCurrentChatIdState(chatId)
      const msgs = await readChatLog(chatId)
      setMessages(msgs || [])
      try {
        const rated = await listChatReactions(chatId)
        setMessageVotes(rated?.votes && typeof rated.votes === 'object' ? rated.votes : {})
      } catch {
        setMessageVotes({})
      }
      try {
        const meta = await getChatMeta(chatId)
        setChatMeta(meta)
        setLivePlan(Array.isArray(meta?.plan) && meta.plan.length ? meta.plan : [])
      } catch {
        setChatMeta(null)
        setLivePlan([])
      }
    } catch (e) {
      console.error(e)
    }
  }, [stopEverything])

  const selectCustomAgent = useCallback(
    async (agent) => {
      if (!agent) return
      setActiveAgentId(agent.id)
      setPanel('chats')
      if (agent.chat_id) {
        await selectChat(agent.chat_id)
      }
    },
    [selectChat],
  )

  const selectJarvis = useCallback(async () => {
    setAgentEditorId(null)
    setPanel('chats')
    const owner = (customAgents || []).find((a) => a.chat_id && a.chat_id === currentChatId)
    if (owner) {
      try {
        const chatId = await createNewChat()
        setCurrentChatIdState(chatId)
        setMessages([])
        await refreshChatList()
      } catch (e) {
        console.error(e)
      }
    }
  }, [customAgents, currentChatId, refreshChatList])

  const handleCreateAgentFromBrief = useCallback(async (brief) => {
    setCreatingAgent(true)
    setCreateAgentError('')
    try {
      const created = await createCustomAgent({ brief })
      await refreshCustomAgents()
      setCreateAgentOpen(false)
      setAgentEditorId(null)
      setActiveAgentId(created.id)
      setPanel('chats')
      if (created.chat_id) await selectChat(created.chat_id)
    } catch (e) {
      setCreateAgentError(e?.message || 'Could not create agent.')
    }
    setCreatingAgent(false)
  }, [refreshCustomAgents, selectChat])

  useEffect(() => {
    refreshLocalModels()
  }, [refreshLocalModels])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await initApiAuth()
      if (cancelled) return
      try {
        if (!workspaceClearedRef.current) {
          const ws = await getWorkspaceStatus()
          if (ws?.linked) {
            setWorkspaceDiskPath(ws.path || '')
            const listed = await listWorkspaceFiles()
            if (!cancelled && listed?.ok && Array.isArray(listed.paths) && listed.paths.length) {
              setWorkspaceRelPaths(listed.paths)
              try {
                sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(listed.paths))
              } catch {
                /* ignore */
              }
              if (ws.label) {
                setWorkspaceLocalLabel((prev) => prev || ws.label)
                try {
                  sessionStorage.setItem('jarvis-workspace-local-label', ws.label)
                } catch {
                  /* ignore */
                }
              }
            }
          }
        }
      } catch {
        /* token or sidecar not ready */
      }
      if (cancelled) return
      refreshChatList()
      refreshCustomAgents()
      try {
        const id = await getCurrentChatId()
        if (cancelled) return
        setCurrentChatIdState(id)
        if (id) await selectChat(id)
      } catch {
        if (!cancelled) setCurrentChatIdState(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refreshChatList, refreshCustomAgents, selectChat])

  useEffect(() => {
    const owner = (customAgents || []).find((a) => a.chat_id && a.chat_id === currentChatId)
    setActiveAgentId(owner ? owner.id : null)
  }, [currentChatId, customAgents])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await initApiAuth()
      if (cancelled) return
      refreshControlPlane()
      refreshMemoryExtras()
    })()
    return () => {
      cancelled = true
    }
  }, [refreshControlPlane, refreshMemoryExtras])

  useEffect(() => {
    if (!sending) return undefined
    const t = setInterval(() => {
      refreshControlPlane()
    }, 2500)
    return () => clearInterval(t)
  }, [sending, refreshControlPlane])

  useEffect(() => {
    if (panel === 'settings') refreshSettings()
  }, [panel, refreshSettings])

  useEffect(() => {
    const busy = localJob?.status === 'downloading' || localJob?.status === 'loading'
    if (panel !== 'settings' && !busy) return undefined
    if (!busy) return undefined
    const t = setInterval(async () => {
      try {
        const job = await getLocalModelJob()
        setLocalJob(job)
        if (job?.status === 'ready' || job?.status === 'error') {
          refreshLocalModels()
          refreshModelSetting()
        }
      } catch {
        /* ignore */
      }
    }, 1000)
    return () => clearInterval(t)
  }, [panel, localJob?.status, refreshLocalModels, refreshModelSetting])

  useEffect(() => {
    refreshPendingApprovals()
    refreshBookmarks()
    refreshUsage()
    const t = setInterval(() => {
      refreshPendingApprovals()
      refreshUsage()
    }, 8000)
    return () => clearInterval(t)
  }, [refreshPendingApprovals, refreshBookmarks, refreshUsage])

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', colorScheme)
    try {
      localStorage.setItem(COLOR_SCHEME_KEY, colorScheme)
    } catch {
      /* ignore */
    }
  }, [colorScheme])

  useEffect(() => {
    try {
      localStorage.setItem(CODING_MODE_KEY, codingModeEnabled ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [codingModeEnabled])

  useEffect(() => {
    if (!codingModeEnabled) {
      setFilePreview(null)
      setPendingWorkspaceEdits(null)
    }
  }, [codingModeEnabled])

  useEffect(() => {
    if (!codingModeEnabled || !workspaceLocalLabel?.trim()) return
    let cancelled = false
    ;(async () => {
      try {
        const rec = await loadProjectRootHandleRecord()
        if (cancelled || !rec?.handle || rec.rootLabel !== workspaceLocalLabel.trim()) return
        if (rec.handle.kind !== 'directory') return
        if (!(await ensureDirectoryReadPermission(rec.handle))) return
        projectRootHandleRef.current = rec.handle
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [codingModeEnabled, workspaceLocalLabel])

  const resolveWorkspaceFileBase = useCallback(async (relPath) => {
    try {
      const disk = await readWorkspaceFile(relPath)
      if (disk?.ok && disk.content) return disk.content
    } catch {
      /* fall through to browser handle / cache */
    }
    let h = projectRootHandleRef.current
    if (!h) {
      try {
        const rec = await loadProjectRootHandleRecord()
        if (
          rec?.handle?.kind === 'directory' &&
          rec.rootLabel === workspaceLocalLabel.trim() &&
          (await ensureDirectoryReadPermission(rec.handle))
        ) {
          projectRootHandleRef.current = rec.handle
          h = rec.handle
        }
      } catch {
        /* ignore */
      }
    }
    if (h) {
      const t = await readProjectFileText(h, relPath)
      if (t) return t
    }
    const label = workspaceLocalLabel.trim()
    if (!label) return ''
    return (await getPreviewFileText(label, relPath)) || ''
  }, [workspaceLocalLabel])

  const saveProjectFile = useCallback(
    async (relPath, text, options = {}) => {
      const skipUndoRecord = !!options.skipUndoRecord
      if (isProjectImagePath(relPath)) {
        return { ok: false, error: 'Image previews are read-only.' }
      }
      let beforeSnapshot = ''
      if (!skipUndoRecord) {
        try {
          beforeSnapshot = await resolveWorkspaceFileBase(relPath)
        } catch {
          beforeSnapshot = ''
        }
      }
      const str = typeof text === 'string' ? text : String(text ?? '')
      try {
        const disk = await writeWorkspaceFile(relPath, str)
        if (disk?.ok) {
          setFilePreview((p) => (p && p.relPath === relPath ? { ...p, body: str, source: 'disk' } : p))
          const wl = workspaceLocalLabel.trim()
          const rel = String(relPath || '').replace(/\\/g, '/')
          if (rel) {
            setWorkspaceRelPaths((prev) => {
              if (prev.includes(rel)) return prev
              const next = [...prev, rel]
              try {
                sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(next))
              } catch {
                /* ignore */
              }
              return next
            })
            workspaceTreeStampRef.current = ''
          }
          if (!skipUndoRecord && wl && beforeSnapshot !== str) {
            void pushWorkspaceEdit(wl, relPath, beforeSnapshot, str)
            setWorkspaceUndoTick((t) => t + 1)
          }
          return { ok: true }
        }
      } catch {
        /* fall through */
      }
      let h = projectRootHandleRef.current
      if (!h) {
        try {
          const rec = await loadProjectRootHandleRecord()
          if (rec?.handle?.kind === 'directory' && rec.rootLabel === workspaceLocalLabel.trim()) {
            if (await ensureDirectoryReadWritePermission(rec.handle)) {
              projectRootHandleRef.current = rec.handle
              h = rec.handle
            }
          }
        } catch {
          /* ignore */
        }
      }
      if (h) {
        if (!(await ensureDirectoryReadWritePermission(h))) {
          return {
            ok: false,
            error: 'Allow read & write access for this folder when the browser prompts.',
          }
        }
        const r = await writeProjectFileText(h, relPath, str)
        if (r.ok) {
          setFilePreview((p) => (p && p.relPath === relPath ? { ...p, body: str, source: 'handle' } : p))
          const wl = workspaceLocalLabel.trim()
          if (!skipUndoRecord && wl && beforeSnapshot !== str) {
            void pushWorkspaceEdit(wl, relPath, beforeSnapshot, str)
            setWorkspaceUndoTick((t) => t + 1)
          }
        }
        return r
      }
      const label = workspaceLocalLabel.trim()
      if (!label) return { ok: false, error: 'No project linked.' }
      const okCache = await putPreviewFileText(label, relPath, str)
      if (okCache) {
        setFilePreview((p) => (p && p.relPath === relPath ? { ...p, body: str, source: 'cache' } : p))
        if (!skipUndoRecord && beforeSnapshot !== str) {
          void pushWorkspaceEdit(label, relPath, beforeSnapshot, str)
          setWorkspaceUndoTick((t) => t + 1)
        }
        return { ok: true, cacheOnly: true }
      }
      return { ok: false, error: 'Could not update cached copy.' }
    },
    [workspaceLocalLabel, resolveWorkspaceFileBase],
  )

  const runOpenWorkspaceFile = useCallback(async (relPath) => {
    const path = String(relPath || '').replace(/\\/g, '/')
    terminalRef.current?.prepareRun?.(path ? `Run ${path}` : 'Run')
    try {
      const r = await runWorkspaceFile(path, 120)
      terminalRef.current?.finishRun?.(r)
      return r
    } catch (e) {
      const r = { ok: false, error: e?.message || String(e) }
      terminalRef.current?.finishRun?.(r)
      return r
    }
  }, [])

  const restoreCheckpointById = useCallback(async (checkpointId) => {
    const r = await restoreCheckpoint(checkpointId)
    if (!r?.ok) {
      window.alert(r?.error || 'Could not restore checkpoint.')
      return false
    }
    await refreshControlPlane()
    const files = Array.isArray(r.restored) ? r.restored.filter(Boolean).join(', ') : r.restored || ''
    window.alert(files ? `Restored checkpoint files: ${files}` : `Restored checkpoint ${r.id || checkpointId}.`)
    return true
  }, [refreshControlPlane])

  const latestRestorableCheckpoint = useMemo(
    () =>
      checkpoints.find((c) => c?.kind === 'workspace_write' || c?.kind === 'overlay_turn') || null,
    [checkpoints],
  )

  const applyWorkspaceRedo = useCallback(async () => {
    const label = workspaceLocalLabel.trim()
    if (!label) return
    const peek = await peekWorkspaceRedo(label)
    if (!peek?.deltas?.length) return
    const results = new Map()
    for (const d of peek.deltas) {
      const r = await saveProjectFile(d.relPath, d.content, { skipUndoRecord: true })
      if (!r?.ok) {
        window.alert(r?.error || 'Could not re-apply one or more files.')
        return
      }
      results.set(d.relPath, r)
    }
    await finalizeWorkspaceRedoPop(label)
    setFilePreview((p) => {
      if (!p) return p
      const d = peek.deltas.find((x) => x.relPath === p.relPath)
      if (!d) return p
      const r = results.get(d.relPath)
      return { ...p, body: d.content, source: r?.cacheOnly ? 'cache' : 'handle' }
    })
    setWorkspaceUndoTick((t) => t + 1)
  }, [workspaceLocalLabel, saveProjectFile])

  useEffect(() => {
    let cancelled = false
    const label = workspaceLocalLabel.trim()
    if (!label || !codingModeEnabled) {
      setWorkspaceUndoUi({ canUndo: false, canRedo: false })
      return
    }
    workspaceUndoStatus(label).then((s) => {
      if (!cancelled) setWorkspaceUndoUi(s)
    })
    return () => {
      cancelled = true
    }
  }, [workspaceLocalLabel, codingModeEnabled, workspaceUndoTick])

  const openProjectFile = useCallback(
    async (relPath) => {
      if (!codingModeEnabled || !workspaceLocalLabel?.trim() || !relPath) return
      setPanel('chats')
      const title = `${workspaceLocalLabel}/${relPath.replace(/\\/g, '/')}`
      const isImage = isProjectImagePath(relPath)
      setFilePreview({
        relPath,
        title,
        kind: isImage ? 'image' : 'text',
        imageUrl: null,
        body: '',
        loading: true,
        error: null,
        source: null,
      })
      try {
        let h = projectRootHandleRef.current
        if (!h) {
          try {
            const rec = await loadProjectRootHandleRecord()
            if (
              rec?.handle?.kind === 'directory' &&
              rec.rootLabel === workspaceLocalLabel.trim() &&
              (await ensureDirectoryReadPermission(rec.handle))
            ) {
              projectRootHandleRef.current = rec.handle
              h = rec.handle
            }
          } catch {
            /* ignore */
          }
        }
        if (isImage) {
          let imageUrl = null
          let source = null
          if (h) {
            imageUrl = await readProjectFileAsDataUrl(h, relPath)
            if (imageUrl) source = 'handle'
          }
          if (!imageUrl) {
            imageUrl = await getPreviewImageDataUrl(workspaceLocalLabel, relPath)
            if (imageUrl) source = 'cache'
          }
          if (!imageUrl) {
            setFilePreview({
              relPath,
              title,
              kind: 'image',
              imageUrl: null,
              body: '',
              loading: false,
              error:
                'Could not load this image. Allow folder access if prompted, or re-import the project so previews are cached.',
              source: null,
            })
            return
          }
          setFilePreview({ relPath, title, kind: 'image', imageUrl, body: '', loading: false, error: null, source })
          return
        }
        let body = null
        let source = null
        if (h) {
          body = await readProjectFileText(h, relPath)
          if (body != null) source = 'handle'
        }
        if (body == null) {
          body = await getPreviewFileText(workspaceLocalLabel, relPath)
          if (body != null) source = 'cache'
        }
        if (body == null) {
          try {
            const remote = await readWorkspaceFile(relPath)
            if (remote?.ok && remote.content != null) {
              body = remote.content
              source = 'workspace'
            }
          } catch {
            /* ignore */
          }
        }
        if (body == null) {
          setFilePreview({
            relPath,
            title,
            kind: 'text',
            imageUrl: null,
            body: '',
            loading: false,
            error:
              'Could not read this file. Allow folder access if the browser asks, or re-import the project folder so previews are cached. Binary or unsupported types may not display as text.',
            source: null,
          })
          return
        }
        setFilePreview({ relPath, title, kind: 'text', imageUrl: null, body, loading: false, error: null, source })
      } catch (e) {
        setFilePreview({
          relPath,
          title,
          kind: isProjectImagePath(relPath) ? 'image' : 'text',
          imageUrl: null,
          body: '',
          loading: false,
          error: e?.message || 'Could not open file.',
          source: null,
        })
      }
    },
    [codingModeEnabled, workspaceLocalLabel, setPanel],
  )

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const connected = params.get('google_connected')
    const err = params.get('google_auth_error')
    if (connected === '1' || err) {
      if (connected === '1') refreshGoogleAuth()
      if (err) alert(`Google sign-in failed: ${err}`)
      params.delete('google_connected')
      params.delete('google_auth_error')
      const q = params.toString()
      const next = `${window.location.pathname}${q ? `?${q}` : ''}${window.location.hash || ''}`
      window.history.replaceState({}, '', next)
    }
  }, [refreshGoogleAuth])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamTimeline])

  useEffect(() => {
    if (!addMenuOpen) return
    const onDown = (e) => {
      if (addMenuRef.current && !addMenuRef.current.contains(e.target)) setAddMenuOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [addMenuOpen])

  useEffect(() => {
    let ws
    let cancelled = false
    ;(async () => {
      await initApiAuth()
      if (cancelled) return
      const url = agentStepsWsUrl()
      ws = new WebSocket(url)
      const bind = (socket) => {
        socket.onmessage = (e) => {
          try {
            const p = JSON.parse(e.data)
            if (p.screenshot == null || p.screenshot === '') return
            if (p.chat_id && currentChatIdRef.current && p.chat_id !== currentChatIdRef.current) return
            if (p.run_id && lastRunIdRef.current && p.run_id !== lastRunIdRef.current) return
            const step = p.step
            if (!lastRunIdRef.current && p.screenshot) {
              screenshotPendingRef.current[step] = p.screenshot
              return
            }
            setStreamTimeline((prev) => {
              const i = prev.findIndex((x) => x.kind === 'step' && x.step === step)
              if (i >= 0) {
                const next = [...prev]
                next[i] = { ...next[i], screenshot: p.screenshot }
                return next
              }
              screenshotPendingRef.current[step] = p.screenshot
              return prev
            })
          } catch {
            /* ignore */
          }
        }
        socket.onclose = () => {
          if (cancelled) return
          setTimeout(() => {
            if (cancelled) return
            try {
              const next = new WebSocket(url)
              bind(next)
              wsRef.current = next
            } catch {
              /* ignore */
            }
          }, 1500)
        }
      }
      bind(ws)
      wsRef.current = ws
    })()
    return () => {
      cancelled = true
      try {
        (wsRef.current || ws)?.close()
      } catch {
        /* ignore */
      }
      wsRef.current = null
    }
  }, [])

  const handleStorageChange = async () => {
    const path = prompt('Enter folder path for chat storage:', storagePath)
    if (path == null || path === '') return
    try {
      await setChatsStoragePath(path)
      setStoragePath(path)
      refreshChatList()
    } catch (err) {
      alert(err?.message || 'Could not change storage location.')
    }
  }

  const handleModelChange = async (e) => {
    const value = e.target.value
    try {
      if (value === 'openai' || value === 'xai') {
        await setModelSetting(value)
        setModelProvider(value)
        return
      }
      const mid = value.startsWith('local:') ? value.slice(6) : localModelId
      if (!mid) return
      const entry = (localModelsInfo?.catalog || []).find((m) => m.id === mid)
      if (entry && !entry.installed) {
        await downloadLocalModel(mid)
        setLocalJob({ status: 'downloading', progress: 1, model_id: mid, message: 'Starting download…' })
        setLocalModelId(mid)
        setModelProvider('local')
        return
      }
      if (entry && entry.installed && !entry.loaded) {
        await loadLocalModel(mid)
      }
      await setModelSetting('local', mid)
      setModelProvider('local')
      setLocalModelId(mid)
      refreshLocalModels()
    } catch (err) {
      alert(err?.message || 'Could not save model setting.')
    }
  }

  const handleAttach = () => {
    fileInputRef.current?.click()
  }

  const onFileChange = (e) => {
    const files = Array.from(e.target.files || [])
    setAttachments((prev) => [...prev, ...files])
    e.target.value = ''
  }

  const removeAttachment = (index) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index))
  }

  const appendMessage = (text, isUser) => {
    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-${prev.length}`, role: isUser ? 'user' : 'assistant', content: text },
    ])
  }

  const appendToolMessage = (toolUsed) => {
    setMessages((prev) => [
      ...prev,
      { id: `${Date.now()}-tool-${prev.length}`, role: 'tool', content: JSON.stringify(toolUsed) },
    ])
  }

  const toggleWebSearchMode = () => {
    setWebSearchMode((m) => {
      const next = !m
      try {
        if (next) sessionStorage.setItem('jarvis-web-search-mode', '1')
        else sessionStorage.removeItem('jarvis-web-search-mode')
      } catch {
        /* ignore */
      }
      return next
    })
    setAddMenuOpen(false)
  }

  const persistLocalProject = (label, snapshot, relPaths = []) => {
    workspaceClearedRef.current = false
    const list = Array.isArray(relPaths) ? relPaths : []
    setWorkspaceLocalLabel(label)
    setWorkspaceRelPaths(list)
    workspaceTreeStampRef.current = ''
    if (snapshot != null) setWorkspaceSnapshot(snapshot)
    try {
      localStorage.removeItem('jarvis-workspace-folder')
      sessionStorage.removeItem('jarvis-workspace-folder')
      sessionStorage.removeItem('jarvis-coding-project-path')
      sessionStorage.removeItem('jarvis-coding-project-mode')
      sessionStorage.setItem('jarvis-workspace-local-label', label)
      sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(list))
      if (snapshot != null) sessionStorage.setItem('jarvis-workspace-snapshot', snapshot)
    } catch (e) {
      try {
        sessionStorage.removeItem('jarvis-workspace-snapshot')
      } catch {
        /* ignore */
      }
      if (e?.name === 'QuotaExceededError') {
        alert(
          'This folder snapshot is too large for browser storage. The file tree is still available; try a smaller folder if chat context looks incomplete.',
        )
      }
    }
  }

  const applyWorkspacePaths = (paths) => {
    const list = Array.isArray(paths) ? paths : []
    setWorkspaceRelPaths((prev) => {
      if (prev.length === list.length && prev.every((p, i) => p === list[i])) return prev
      return list
    })
    try {
      sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(list))
    } catch {
      /* ignore */
    }
  }

  const reloadWorkspaceTree = async () => {
    if (workspaceClearedRef.current) return
    setProjectImportBusy(true)
    try {
      await initApiAuth()
      const listed = await listWorkspaceFiles()
      if (listed?.ok && Array.isArray(listed.paths) && listed.paths.length) {
        applyWorkspacePaths(listed.paths)
        workspaceTreeStampRef.current = ''
      }
    } catch (e) {
      alert(e?.message || 'Could not load the file list.')
    } finally {
      setProjectImportBusy(false)
    }
  }

  useEffect(() => {
    if (!codingModeEnabled) return
    const watching = Boolean(workspaceDiskPath || workspaceLocalLabel.trim() || projectRootHandleRef.current)
    if (!watching) return
    let cancelled = false

    const tick = async () => {
      if (cancelled || workspaceClearedRef.current) return
      try {
        const st = await workspaceTreeStamp()
        if (!cancelled && st?.ok && st.stamp && st.stamp !== workspaceTreeStampRef.current) {
          workspaceTreeStampRef.current = st.stamp
          const listed = await listWorkspaceFiles()
          if (!cancelled && listed?.ok && Array.isArray(listed.paths)) {
            applyWorkspacePaths(listed.paths)
          }
        }
      } catch {
        const h = projectRootHandleRef.current
        if (!h || cancelled) return
        try {
          const paths = await listTreePathsFromDirectoryHandle(h)
          if (!cancelled && paths.length) applyWorkspacePaths(paths)
        } catch {
          /* ignore */
        }
      }
    }

    tick()
    const id = window.setInterval(tick, 2000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [codingModeEnabled, workspaceDiskPath, workspaceLocalLabel])

  const clearProjectContext = () => {
    workspaceClearedRef.current = true
    workspaceTreeStampRef.current = ''
    const label = workspaceLocalLabel.trim()
    if (label) {
      clearPreviewCacheForRoot(label).catch(() => {})
      clearWorkspaceUndo(label).catch(() => {})
    }
    projectRootHandleRef.current = null
    clearProjectRootHandleRecord().catch(() => {})
    unlinkWorkspace().catch(() => {})
    setWorkspaceDiskPath('')
    setFilePreview(null)
    setPendingWorkspaceEdits(null)
    setWorkspaceLocalLabel('')
    setWorkspaceSnapshot('')
    setWorkspaceRelPaths([])
    try {
      localStorage.removeItem('jarvis-workspace-folder')
      sessionStorage.removeItem('jarvis-workspace-folder')
      sessionStorage.removeItem('jarvis-workspace-snapshot')
      sessionStorage.removeItem('jarvis-workspace-local-label')
      sessionStorage.removeItem('jarvis-workspace-paths')
      sessionStorage.removeItem('jarvis-coding-project-path')
      sessionStorage.removeItem('jarvis-coding-project-mode')
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    if (!workspaceSnapshot.trim() || !workspaceLocalLabel.trim()) return
    if (workspaceRelPaths.length > 0) return
    try {
      const raw = sessionStorage.getItem('jarvis-workspace-paths')
      const parsed = raw ? JSON.parse(raw) : null
      if (Array.isArray(parsed) && parsed.length > 0) return
    } catch {
      /* fall through */
    }
    const parsed = parseRelPathsFromSnapshotMarkdown(workspaceSnapshot, workspaceLocalLabel)
    if (parsed.length) {
      setWorkspaceRelPaths(parsed)
      try {
        sessionStorage.setItem('jarvis-workspace-paths', JSON.stringify(parsed))
      } catch {
        /* ignore */
      }
    }
  }, [workspaceSnapshot, workspaceLocalLabel, workspaceRelPaths.length])

  const pickLocalProjectFolder = async () => {
    if (!codingModeEnabled) return
    workspaceClearedRef.current = false
    const nativePath = await pickWorkspaceFolderNative()
    if (nativePath) {
      setProjectImportBusy(true)
      try {
        const linked = await linkWorkspace(nativePath)
        if (!linked?.ok) {
          alert(linked?.error || 'Could not link that folder.')
          return
        }
        const snap = await fetchWorkspaceSnapshot()
        let paths = Array.isArray(snap?.rel_paths) ? snap.rel_paths : []
        if (!paths.length) {
          try {
            const listed = await listWorkspaceFiles()
            if (listed?.ok && Array.isArray(listed.paths)) paths = listed.paths
          } catch {
            /* ignore */
          }
        }
        persistLocalProject(snap?.label || linked.label || 'project', snap?.snapshot || '', paths)
        setWorkspaceDiskPath(snap?.path || nativePath)
      } catch (e) {
        alert(e?.message || 'Could not open that folder.')
      } finally {
        setProjectImportBusy(false)
      }
      return
    }
    if (isDesktopShell()) return
    const typed = window.prompt('Folder path on this computer (or cancel to use the browser picker):', workspaceDiskPath || '')
    if (typed && typed.trim()) {
      setProjectImportBusy(true)
      try {
        const linked = await linkWorkspace(typed.trim())
        if (!linked?.ok) {
          alert(linked?.error || 'Could not link that folder.')
          return
        }
        const snap = await fetchWorkspaceSnapshot()
        let paths = Array.isArray(snap?.rel_paths) ? snap.rel_paths : []
        if (!paths.length) {
          try {
            const listed = await listWorkspaceFiles()
            if (listed?.ok && Array.isArray(listed.paths)) paths = listed.paths
          } catch {
            /* ignore */
          }
        }
        persistLocalProject(snap?.label || linked.label || 'project', snap?.snapshot || '', paths)
        setWorkspaceDiskPath(snap?.path || typed.trim())
      } catch (e) {
        alert(e?.message || 'Could not open that folder.')
      } finally {
        setProjectImportBusy(false)
      }
      return
    }
    if (canUseDirectoryPicker()) {
      try {
        const handle = await window.showDirectoryPicker()
        const rootName = handle.name || 'project'
        projectRootHandleRef.current = handle
        setProjectImportBusy(true)
        const { snapshot, relPaths } = await buildSnapshotFromDirectoryHandle(handle, rootName)
        if (snapshot) {
          try {
            persistLocalProject(rootName, snapshot, relPaths)
          } catch {
            /* quota handled inside */
          }
        }
        saveProjectRootHandleRecord(handle, rootName).catch(() => {})
        return
      } catch (e) {
        if (e?.name === 'AbortError') return
        console.warn(e)
      } finally {
        setProjectImportBusy(false)
      }
    }
    projectRootHandleRef.current = null
    projectFolderInputRef.current?.click()
  }

  const onProjectFolderInputChange = async (e) => {
    if (!codingModeEnabled) {
      e.target.value = ''
      return
    }
    const fl = e.target.files
    e.target.value = ''
    if (!fl?.length) return
    clearProjectRootHandleRecord().catch(() => {})
    setProjectImportBusy(true)
    try {
      const { snapshot, relPaths } = await buildSnapshotFromFileList(fl)
      const root = fl[0].webkitRelativePath.split(/[/\\]/)[0] || 'project'
      projectRootHandleRef.current = null
      if (snapshot) {
        try {
          persistLocalProject(root, snapshot, relPaths)
        } catch {
          /* quota handled inside */
        }
      }
    } catch (err) {
      alert(err?.message || 'Could not read that folder.')
    } finally {
      setProjectImportBusy(false)
    }
  }

  const workspaceDisplayLabel = () => workspaceLocalLabel.trim() || ''

  const beginIntroduceWizard = async () => {
    let profile
    try {
      profile = await getUserProfile()
    } catch {
      profile = structuredClone(EMPTY_USER_PROFILE)
    }
    const total = INTRODUCE_STEP_COUNT
    const firstBody = `${formatIntroduceWelcome(total)}${formatIntroduceQuestion(0, total)}`
    const initial = { stepIndex: 0, profile }
    introduceWizardRef.current = initial
    setIntroduceWizard(initial)
    appendMessage('/introduce', true)
    try {
      await appendChatLog('user', '/introduce')
    } catch {
      /* ignore */
    }
    appendMessage(firstBody, false)
    try {
      await appendChatLog('assistant', firstBody)
    } catch {
      /* ignore */
    }
    refreshChatList()
  }

  const continueIntroduceWizard = async (answerRaw) => {
    const w = introduceWizardRef.current
    if (!w) return
    appendMessage(answerRaw, true)
    try {
      await appendChatLog('user', answerRaw)
    } catch {
      /* ignore */
    }

    const step = INTRODUCE_STEPS[w.stepIndex]
    const v = normalizeProfileAnswer(answerRaw)
    const profile = structuredClone(w.profile)
    if (step.appendix) {
      if (v) {
        profile.appendix_notes = [...(profile.appendix_notes || []), v]
      }
    } else {
      setUserProfileField(profile, step.key, v)
    }

    const nextIdx = w.stepIndex + 1
    const total = INTRODUCE_STEP_COUNT
    if (nextIdx >= total) {
      try {
        await saveUserProfile(profile)
        const jsonBlock = JSON.stringify(profile, null, 2)
        const doneMsg = `**Profile saved** to \`backend/memory/user_profile.json\`.\n\n\`\`\`json\n${jsonBlock}\n\`\`\`\n\nRun \`/introduce\` again anytime to update your answers.`
        appendMessage(doneMsg, false)
        try {
          await appendChatLog('assistant', doneMsg)
        } catch {
          /* ignore */
        }
      } catch (e) {
        const err = e?.message || 'Could not save profile.'
        const failMsg = `**Profile not saved:** ${err}\n\nYour answers are in this chat. Try again when the API is running, or copy the JSON below.\n\n\`\`\`json\n${JSON.stringify(profile, null, 2)}\n\`\`\``
        appendMessage(failMsg, false)
        try {
          await appendChatLog('assistant', failMsg)
        } catch {
          /* ignore */
        }
      }
      introduceWizardRef.current = null
      setIntroduceWizard(null)
    } else {
      const q = formatIntroduceQuestion(nextIdx, total)
      const next = { stepIndex: nextIdx, profile }
      introduceWizardRef.current = next
      setIntroduceWizard(next)
      appendMessage(q, false)
      try {
        await appendChatLog('assistant', q)
      } catch {
        /* ignore */
      }
    }
    refreshChatList()
  }

  const handleSend = async (opts = {}) => {
    const raw = (opts.text != null ? String(opts.text) : input).trim()
    const explicitWs = (opts.webSearchQuery || '').trim()
    const extraWs =
      explicitWs || (webSearchMode && raw ? raw : '')
    const filesToSend = [...attachments]
    const cSnap = workspaceSnapshot.trim()
    const projectContextActive = codingModeEnabled && cSnap.length > 0
    if (/^\/help\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      setFileMention(null)
      setAttachments([])
      appendMessage('/help', true)
      try {
        await appendChatLog('user', '/help')
      } catch {
        /* ignore */
      }
      appendMessage(CHAT_HELP_MANUAL_MARKDOWN, false)
      try {
        await appendChatLog('assistant', CHAT_HELP_MANUAL_MARKDOWN)
      } catch {
        /* ignore */
      }
      refreshChatList()
      return
    }
    if (/^\/stop\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      await stopEverything()
      return
    }
    if (/^\/compact\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      appendMessage('/compact', true)
      try {
        await appendChatLog('user', '/compact')
      } catch {
        /* ignore */
      }
      try {
        const cid = currentChatId || (await getCurrentChatId())
        const data = await compactChat(cid)
        const body = data?.summary || 'No summary.'
        appendMessage(body, false)
        await appendChatLog('assistant', body)
      } catch (e) {
        appendMessage(e?.message || 'Compact failed.', false)
      }
      refreshChatList()
      return
    }
    if (/^\/handoff\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      appendMessage('/handoff', true)
      try {
        await appendChatLog('user', '/handoff')
      } catch {
        /* ignore */
      }
      try {
        const cid = currentChatId || (await getCurrentChatId())
        const data = await getChatHandoff(cid)
        const body = data?.markdown || 'No handoff.'
        try {
          await navigator.clipboard.writeText(body)
        } catch {
          /* ignore */
        }
        appendMessage(`${body}\n\n_Copied to clipboard when the browser allowed it._`, false)
        await appendChatLog('assistant', body)
      } catch (e) {
        appendMessage(e?.message || 'Handoff failed.', false)
      }
      refreshChatList()
      return
    }
    if (/^\/recap\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      appendMessage('/recap', true)
      try {
        await appendChatLog('user', '/recap')
      } catch {
        /* ignore */
      }
      try {
        const cid = currentChatId || (await getCurrentChatId())
        const data = await getChatRecap(cid)
        const body = data?.markdown || 'No recap.'
        appendMessage(body, false)
        await appendChatLog('assistant', body)
      } catch (e) {
        appendMessage(e?.message || 'Recap failed.', false)
      }
      refreshChatList()
      return
    }
    if (/^\/(?:btw|side)\s+/i.test(raw) && filesToSend.length === 0) {
      const q = raw.replace(/^\/(?:btw|side)\s+/i, '').trim()
      setInput('')
      appendMessage(raw, true)
      try {
        await appendChatLog('user', raw)
      } catch {
        /* ignore */
      }
      const ac = new AbortController()
      abortRef.current = ac
      sendingRef.current = true
      setSending(true)
      try {
        const data = await chatbotResponse(
          `Side question (do not start an agent plan; answer only):\n${q}`,
          null,
          null,
          ac.signal,
        )
        if (data?.run_id) lastRunIdRef.current = data.run_id
        const reply = typeof data === 'string' ? data : data?.reply
        const body = `**Side note** (main task unchanged)\n\n${reply || '(no reply)'}`
        appendMessage(body, false)
        await appendChatLog('assistant', body)
      } catch (e) {
        const aborted = e?.name === 'AbortError' || /aborted/i.test(e?.message || '')
        appendMessage(aborted ? 'Stopped.' : e?.message || 'Side question failed.', false)
      }
      abortRef.current = null
      sendingRef.current = false
      setSending(false)
      refreshChatList()
      return
    }
    const slashLocal = raw.match(/^\/([a-zA-Z][\w:-]*)(?:\s+([\s\S]*))?$/)
    const slashReserved = /^(help|stop|compact|handoff|recap|btw|side|search-memory|introduce)$/i
    if (slashLocal && !slashReserved.test(slashLocal[1]) && filesToSend.length === 0 && !opts.skipSlash) {
      const cmd = slashLocal[1]
      const rest = (slashLocal[2] || '').trim()
      if (/^undo$/i.test(cmd) && latestRestorableCheckpoint) {
        setInput('')
        setFileMention(null)
        appendMessage(raw, true)
        try {
          await appendChatLog('user', raw)
        } catch {
          /* ignore */
        }
        const ok = await restoreCheckpointById(latestRestorableCheckpoint.id)
        const body = ok
          ? `Restored checkpoint \`${latestRestorableCheckpoint.id}\` (${latestRestorableCheckpoint.kind}).`
          : 'Could not restore checkpoint.'
        appendMessage(body, false)
        try {
          await appendChatLog('assistant', body)
        } catch {
          /* ignore */
        }
        refreshChatList()
        return
      }
      try {
        const cid = currentChatId || (await getCurrentChatId())
        const data = await runSlash(cmd, rest, cid)
        setInput('')
        setFileMention(null)
        if (data?.set_mode) {
          try {
            await setRunMode(data.set_mode)
            setRunModeState(data.set_mode)
          } catch {
            /* ignore */
          }
        }
        if (data?.kind === 'notify') {
          const perm = await ensureNotifyPermission()
          notifyJarvis('Jarvis', perm === 'granted' ? 'Notifications are on.' : 'Notifications were blocked.')
        }
        if (data?.set_provider) {
          try {
            await refreshModelSetting()
          } catch {
            /* ignore */
          }
        }
        if (/^(loop|proactive)$/i.test(cmd) && /^(stop|clear|off)$/i.test(rest) && loopPollRef.current) {
          clearInterval(loopPollRef.current)
          loopPollRef.current = null
        }
        if (data?.kind === 'loop') {
          if (loopPollRef.current) clearInterval(loopPollRef.current)
          loopPollRef.current = setInterval(async () => {
            const cid = currentChatIdRef.current
            if (!cid) return
            try {
              const msgs = await readChatLog(cid)
              setMessages(msgs || [])
            } catch {
              /* ignore */
            }
          }, 12000)
        }
        if ((data?.kind === 'export' || data?.kind === 'copy') && data.text) {
          try {
            await navigator.clipboard.writeText(data.text)
          } catch {
            /* ignore */
          }
        }
        if (data?.kind === 'export' && data.text) {
          const blob = new Blob([data.text], { type: 'text/markdown;charset=utf-8' })
          const url = URL.createObjectURL(blob)
          const a = document.createElement('a')
          a.href = url
          a.download = data.filename || 'jarvis-chat.md'
          a.click()
          URL.revokeObjectURL(url)
        }
        if (data?.kind === 'prompt' && data.prompt) {
          await handleSend({ text: data.prompt, skipSlash: true })
          return
        }
        appendMessage(raw, true)
        try {
          await appendChatLog('user', raw)
        } catch {
          /* ignore */
        }
        const body = data?.markdown || data?.error || 'Done.'
        appendMessage(body, false)
        try {
          await appendChatLog('assistant', body)
        } catch {
          /* ignore */
        }
        if (data?.reload && cid) {
          await selectChat(cid)
        }
        refreshChatList()
        return
      } catch (e) {
        if (!/unknown command/i.test(e?.message || '')) {
          setInput('')
          appendMessage(raw, true)
          appendMessage(e?.message || 'Command failed.', false)
          refreshChatList()
          return
        }
      }
    }
    if (/^\/search-memory\s+/i.test(raw) && filesToSend.length === 0) {
      const q = raw.replace(/^\/search-memory\s+/i, '').trim()
      setInput('')
      appendMessage(raw, true)
      try {
        await appendChatLog('user', raw)
      } catch {
        /* ignore */
      }
      try {
        const data = await searchChats(q)
        const hits = data?.hits || []
        const body = hits.length
          ? ['# Memory search', '', ...hits.map((h) => `- **${h.title || h.id}** — ${h.snippet || ''}`)].join('\n')
          : 'No matching chats.'
        appendMessage(body, false)
        await appendChatLog('assistant', body)
      } catch (e) {
        appendMessage(e?.message || 'Search failed.', false)
      }
      refreshChatList()
      return
    }
    if (/^\/introduce\/?\s*$/i.test(raw) && filesToSend.length === 0) {
      setInput('')
      setFileMention(null)
      setAttachments([])
      await beginIntroduceWizard()
      return
    }
    if (introduceWizardRef.current && filesToSend.length > 0 && !/^\/introduce\/?\s*$/i.test(raw)) {
      const warn =
        '**Attachments are not used during `/introduce`.** Remove files from the composer, then answer the last question (or send `/introduce` to start over).'
      appendMessage(warn, false)
      try {
        await appendChatLog('assistant', warn)
      } catch {
        /* ignore */
      }
      refreshChatList()
      return
    }
    if (introduceWizardRef.current && filesToSend.length === 0 && raw) {
      setInput('')
      setFileMention(null)
      setAttachments([])
      await continueIntroduceWizard(raw)
      return
    }
    if (!raw && filesToSend.length === 0 && !extraWs && !projectContextActive) {
      return
    }
    if (sendingRef.current && !opts.fromQueue) {
      if (raw) {
        messageQueueRef.current = [...messageQueueRef.current, raw]
        setMessageQueue(messageQueueRef.current)
        setInput('')
        setFileMention(null)
      }
      return
    }
    setInput('')
    setFileMention(null)
    setAttachments([])

    let displayText = raw
    if (filesToSend.length === 1 && !displayText) displayText = filesToSend[0].name
    else if (filesToSend.length > 1 && !displayText) displayText = filesToSend.map((f) => f.name).join(', ')
    if (explicitWs && !webSearchMode) {
      displayText = displayText ? `${displayText} · Web: ${explicitWs}` : `Web search: ${explicitWs}`
    }
    if (projectContextActive) {
      const label = workspaceDisplayLabel() || 'project'
      displayText = displayText
        ? `${displayText} · Project: ${label}`
        : `Project: ${label}`
    }
    appendMessage(displayText, true)
    sendingRef.current = true
    setSending(true)
    const ac = new AbortController()
    abortRef.current = ac
    lastRunIdRef.current = null
    setLiveReply('')
    streamJarvisStripRef.current = ''
    setStreamTimeline([])
    setLivePlan([])
    screenshotPendingRef.current = {}

    try {
      await appendChatLog('user', displayText, currentChatId)
    } catch {
      /* ignore */
    }

    let chatId = currentChatId
    try {
      if (!chatId) {
        chatId = (await getCurrentChatId()) || null
        setCurrentChatIdState(chatId)
      }
      let reply
      if (filesToSend.length > 0) {
        const fileResult = await sendMessageWithFiles(
          raw || 'Please summarize or answer based on the attached documents.',
          filesToSend,
          chatId,
          extraWs.trim() || null,
          projectContextActive,
          projectContextActive ? cSnap || null : null,
          activeAgentId || null,
          ac.signal,
          opts.resumeTaskId || null,
        )
        if (fileResult?.run_id) lastRunIdRef.current = fileResult.run_id
        reply = typeof fileResult === 'string' ? fileResult : fileResult?.reply
        appendMessage(reply, false)
        await appendChatLog('assistant', reply, chatId)
      } else {
        const formatAgentStep = (d) => {
          const n = d.step
          const thought = (d.thought || '').trim()
          const desc = (d.description || '').trim()
          const res = d.result != null ? String(d.result).trim() : ''
          const lower = `${desc} ${thought}`.toLowerCase()
          const retry = lower.includes('retry') || lower.includes('trying again')
          if (n === 0) {
            return {
              kind: 'step',
              phase: 'plan',
              message: 'Forming a plan',
              detail: desc || thought,
              step: 0,
              screenshot: d.screenshot || null,
            }
          }
          let message = `Implementing step ${n}: ${d.action || 'action'}`
          if (retry) message = `Step ${n} failed — trying again`
          const detail = [desc || thought, res ? `→ ${res}` : ''].filter(Boolean).join(' ').slice(0, 600)
          return {
            kind: 'step',
            phase: 'run',
            message,
            detail,
            step: n,
            screenshot: d.screenshot || null,
          }
        }

        const streamMsg =
          raw ||
          (extraWs
            ? ''
            : projectContextActive
              ? ''
              : 'Please summarize or answer based on the attached documents.')
        const streamResult = await sendMessageStream(streamMsg, null, chatId, {
            signal: ac.signal,
            onRun: (id) => {
              lastRunIdRef.current = id
            },
            webSearchQuery: extraWs.trim() || null,
            codingMode: projectContextActive,
            codingProjectSnapshot: projectContextActive ? cSnap || null : null,
            customAgentId: activeAgentId || null,
            resumeTaskId: opts.resumeTaskId || null,
            onUsage: (u) => setLiveUsage(u),
            onChunk: (delta) => {
              streamJarvisStripRef.current += delta
              setLiveReply(stripJarvisFileFencesForDisplay(streamJarvisStripRef.current))
            },
            onStatus: (d) => {
              if (d.phase === 'done') return
              setStreamTimeline((prev) => [
                ...prev.slice(-79),
                {
                  kind: 'status',
                  phase: d.phase,
                  message: d.message || d.phase,
                  detail:
                    d.next_steps ||
                    d.reasoning ||
                    (d.goal && d.phase === 'supervisor_done' ? `Goal: ${d.goal}` : '') ||
                    '',
                },
              ])
            },
            onAgentStep: (d) => {
              if (d.action === 'update_plan') {
                let parsed = d.result
                if (typeof parsed === 'string') {
                  try {
                    parsed = JSON.parse(parsed)
                  } catch {
                    parsed = null
                  }
                }
                if (Array.isArray(parsed?.plan)) setLivePlan(parsed.plan)
              }
              const row = formatAgentStep(d)
              const pending = screenshotPendingRef.current[d.step]
              if (pending != null) {
                delete screenshotPendingRef.current[d.step]
              }
              const screenshot = d.screenshot || pending || row.screenshot || null
              setStreamTimeline((prev) => [...prev.slice(-79), { ...row, screenshot }])
            },
        })
        reply = streamResult?.reply ?? streamResult ?? ''
        if (streamResult?.run_id) lastRunIdRef.current = streamResult.run_id
        if (streamResult?.tool_used) appendToolMessage(streamResult.tool_used)
        if (streamResult?.file_edits?.length) {
          setPendingWorkspaceEdits({ id: Date.now(), files: streamResult.file_edits })
        }
        if (streamResult?.pending_approvals?.length) {
          setPendingApprovals(streamResult.pending_approvals)
          notifyJarvis('Jarvis needs approval', streamResult.pending_approvals[0]?.summary || 'A write is waiting.')
        } else {
          refreshPendingApprovals()
          notifyJarvis('Jarvis finished', String(streamResult?.reply || reply || 'Done.').split('\n')[0])
        }
        appendMessage(reply || '', false)
        await appendChatLog('assistant', reply || '', chatId)
        refreshControlPlane()
        try {
          const meta = await getChatMeta(chatId)
          setChatMeta(meta)
          if (Array.isArray(meta?.plan) && meta.plan.length) setLivePlan(meta.plan)
        } catch {
          /* ignore */
        }
      }
      setLiveReply(null)
      setStreamTimeline([])
    } catch (err) {
      setLiveReply(null)
      setStreamTimeline([])
      screenshotPendingRef.current = {}
      const aborted = err?.name === 'AbortError' || /aborted/i.test(err?.message || '')
      const msg = aborted
        ? 'Stopped.'
        : err?.message || 'Sorry, something went wrong. Please try again.'
      appendMessage(msg, false)
      try {
        await appendChatLog('assistant', msg, chatId || currentChatId)
      } catch {
        /* ignore */
      }
    }
    abortRef.current = null
    sendingRef.current = false
    setSending(false)
    refreshChatList()
    refreshUsage()
    const queued = messageQueueRef.current
    if (queued.length && !queueHeldRef.current) {
      const [next, ...rest] = queued
      messageQueueRef.current = rest
      setMessageQueue(rest)
      setTimeout(() => handleSend({ text: next, fromQueue: true }), 0)
    }
  }

  const applyAllPendingEdits = useCallback(async () => {
    const files = visibleWorkspaceEditsSession?.files || []
    for (const f of files) {
      const r = await saveProjectFile(f.path, f.content)
      if (!r?.ok) {
        alert(r?.error || `Could not apply ${f.path}`)
        return
      }
    }
    setPendingWorkspaceEdits(null)
    setWorkspaceReviewHiddenPaths(new Set())
  }, [visibleWorkspaceEditsSession, saveProjectFile])

  const discardAllPendingEdits = useCallback(() => {
    setPendingWorkspaceEdits(null)
    setWorkspaceReviewHiddenPaths(new Set())
  }, [])

  const handleKeyDown = (e) => {
    const su = slashUiRef.current
    if (su && su.matches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSlashMention((sm) => {
          if (!sm?.matches?.length) return sm
          return { ...sm, highlight: (sm.highlight + 1) % sm.matches.length }
        })
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSlashMention((sm) => {
          if (!sm?.matches?.length) return sm
          const n = sm.matches.length
          return { ...sm, highlight: (sm.highlight + n - 1) % n }
        })
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const hit = su.matches[su.highlight]
        if (hit?.name) applySlashMention(hit.name)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSlashMention(null)
        return
      }
    }
    const mu = mentionUiRef.current
    if (mu && mu.matches.length > 0) {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        bumpFileMentionHighlight(1)
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        bumpFileMentionHighlight(-1)
        return
      }
      if (e.key === ' ' || e.code === 'Space') {
        e.preventDefault()
        const rel = mu.matches[mu.highlight]
        if (rel) toggleFileMentionSelect(rel)
        return
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        e.preventDefault()
        const picked = mu.selectedPaths?.length
          ? mu.selectedPaths
          : [mu.matches[mu.highlight]].filter(Boolean)
        applyFileMentionMany(picked)
        return
      }
    }
    if (mu && e.key === 'Escape') {
      e.preventDefault()
      setFileMention(null)
      return
    }
    if (e.key === 'Tab' && sendingRef.current && input.trim()) {
      e.preventDefault()
      const text = input.trim()
      messageQueueRef.current = [...messageQueueRef.current, text]
      setMessageQueue(messageQueueRef.current)
      setInput('')
      return
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      if (sendingRef.current && input.trim()) {
        const text = input.trim()
        messageQueueRef.current = [...messageQueueRef.current, text]
        setMessageQueue(messageQueueRef.current)
        setInput('')
        return
      }
      handleSend({})
    }
  }

  const handleForkFrom = async (messageIndex) => {
    if (!currentChatId) return
    try {
      const data = await forkChat(currentChatId, messageIndex)
      if (data?.id) {
        await refreshChatList()
        await selectChat(data.id)
      }
    } catch (e) {
      alert(e?.message || 'Fork failed.')
    }
  }

  const handleMergeBranch = async () => {
    if (!currentChatId || !chatMeta?.parent_id) return
    if (!confirm('Merge this branch’s new messages into the parent chat?')) return
    try {
      const data = await mergeChat(currentChatId)
      if (data?.target_id) {
        await refreshChatList()
        await selectChat(data.target_id)
      }
    } catch (e) {
      alert(e?.message || 'Merge failed.')
    }
  }

  const displayMessages = messages.length > 200 ? messages.slice(-200) : messages
  const displayOffset = messages.length > 200 ? messages.length - 200 : 0

  const chatMainInner = (
    <>
      {chatMeta?.goal_condition ? (
        <div className="agent-chat-banner">
          <span className="agent-chat-banner__id">
            <strong>Goal</strong>
            <span className="agent-chat-banner__title">{chatMeta.goal_condition}</span>
          </span>
          <button
            type="button"
            className="agent-chat-banner__btn"
            onClick={async () => {
              try {
                await runSlash('goal', 'clear', currentChatId)
                if (currentChatId) await selectChat(currentChatId)
              } catch {
                /* ignore */
              }
            }}
          >
            Clear
          </button>
        </div>
      ) : null}
      {livePlan.length ? (
        <ol className="swe-todo-strip" aria-label="Agent plan">
          {livePlan.map((item) => (
            <li key={item.id || item.text} className={`swe-todo-strip__item is-${item.status || 'pending'}`}>
              <span className="swe-todo-strip__mark" aria-hidden>
                {item.status === 'done' ? '✓' : item.status === 'active' ? '▸' : '○'}
              </span>
              <span>{item.text || item.id}</span>
            </li>
          ))}
        </ol>
      ) : null}
      {visibleWorkspaceEditsSession?.files?.length ? (
        <div className="workspace-apply-bar" role="region" aria-label="Proposed file changes">
          <span className="workspace-apply-bar__label">
            {visibleWorkspaceEditsSession.files.length} file
            {visibleWorkspaceEditsSession.files.length === 1 ? '' : 's'} ready to apply
          </span>
          <button type="button" className="workspace-apply-bar__btn" onClick={applyAllPendingEdits}>
            Apply all
          </button>
          <button type="button" className="workspace-apply-bar__btn workspace-apply-bar__btn--ghost" onClick={discardAllPendingEdits}>
            Discard
          </button>
        </div>
      ) : null}
      {chatMeta?.parent_id ? (
        <div className="agent-chat-banner">
          <span className="agent-chat-banner__id">
            <strong>{chatMeta.branch_label || 'Branch'}</strong>
            <span className="agent-chat-banner__title">forked conversation</span>
          </span>
          <button type="button" className="agent-chat-banner__btn" onClick={() => selectChat(chatMeta.parent_id)}>
            Parent
          </button>
          <button type="button" className="agent-chat-banner__btn" onClick={handleMergeBranch}>
            Merge back
          </button>
        </div>
      ) : null}
      {activeAgent ? (
        <div className="agent-chat-banner">
          <span className="agent-chat-banner__id">
            <span aria-hidden>{activeAgent.emoji || '✦'}</span>
            <strong>{activeAgent.name}</strong>
            {activeAgent.title ? <span className="agent-chat-banner__title">{activeAgent.title}</span> : null}
          </span>
          <button
            type="button"
            className="agent-chat-banner__btn"
            onClick={() => setAgentEditorId(activeAgent.id)}
          >
            Configure
          </button>
        </div>
      ) : null}
      <div className="chat-messages">
        {displayMessages.map((msg, i) => (
          <div key={msg.id || `${msg.role}-${i}-${(msg.content || '').slice(0, 24)}`} className={`msg ${msg.role === 'user' ? 'msg-user' : msg.role === 'tool' ? 'msg-tool' : 'msg-bot'}`}>
            {msg.role === 'user' ? (
              <div className="msg-user-body">
                <span className="msg-text">{msg.content}</span>
                {currentChatId ? (
                  <button
                    type="button"
                    className="msg-pin-btn"
                    title="Fork a new chat from this message"
                    onClick={() => handleForkFrom(displayOffset + i)}
                  >
                    Fork
                  </button>
                ) : null}
              </div>
            ) : msg.role === 'tool' ? (
              <ToolMessageCard content={msg.content} />
            ) : (
              <div className="msg-bot-body">
                <div className="msg-markdown">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    urlTransform={markdownUrlTransform}
                    components={workspaceEditMarkdownComponents}
                  >
                    {msg.content}
                  </ReactMarkdown>
                </div>
                <div className="msg-bot-actions">
                  <CopyResponseButton text={msg.content} />
                  {(() => {
                    const excerpt = (msg.content || '').slice(0, 240)
                    const vote = messageVotes[excerpt]
                    const busy = reactionBusyKey === excerpt
                    const rate = async (next) => {
                      setReactionBusyKey(excerpt)
                      setMessageVotes((prev) => ({ ...prev, [excerpt]: next }))
                      try {
                        await reactToReply(currentChatId || '', next, excerpt, msg.id || '')
                      } catch (e) {
                        setMessageVotes((prev) => {
                          const copy = { ...prev }
                          delete copy[excerpt]
                          return copy
                        })
                        alert(e?.message || 'Could not save that rating.')
                      } finally {
                        setReactionBusyKey('')
                      }
                    }
                    return (
                      <>
                        <button
                          type="button"
                          className={`msg-pin-btn msg-react-btn${vote === 'up' ? ' is-on' : ''}`}
                          title="This reply helped — Jarvis keeps it in mind"
                          disabled={busy}
                          onClick={() => rate('up')}
                        >
                          👍
                        </button>
                        <button
                          type="button"
                          className={`msg-pin-btn msg-react-btn${vote === 'down' ? ' is-on' : ''}`}
                          title="This reply missed — Jarvis notes it and tries another approach"
                          disabled={busy}
                          onClick={() => rate('down')}
                        >
                          👎
                        </button>
                      </>
                    )
                  })()}
                  <button
                    type="button"
                    className="msg-pin-btn"
                    title="Fork a new chat from this reply"
                    onClick={() => handleForkFrom(displayOffset + i)}
                  >
                    Fork
                  </button>
                  {displayOffset + i === messages.length - 1 ? (
                    <button
                      type="button"
                      className="msg-pin-btn"
                      title="Rewind: drop this reply (Claude /rewind)"
                      onClick={async () => {
                        if (!currentChatId) return
                        if (!confirm('Drop the last assistant reply from this chat?')) return
                        try {
                          await rewindChat(currentChatId)
                          await selectChat(currentChatId)
                          await refreshChatList()
                        } catch (e) {
                          alert(e?.message || 'Rewind failed.')
                        }
                      }}
                    >
                      Rewind
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="msg-pin-btn"
                    title="Pin this reply"
                    onClick={async () => {
                      try {
                        await addBookmark(currentChatId || '', msg.content, (msg.content || '').slice(0, 60))
                        refreshBookmarks()
                      } catch (e) {
                        alert(e?.message || 'Could not pin.')
                      }
                    }}
                  >
                    Pin
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
        {(sending || liveReply || streamTimeline.length > 0) && (
          <div className="msg msg-bot msg-streaming">
            {streamTimeline.length > 0 && (
              <div className="stream-timeline" aria-live="polite">
                {streamTimeline.map((item, i) => (
                  <div
                    key={i}
                    className={`stream-timeline-row stream-timeline-${item.kind} stream-phase-${item.phase || ''}${item.screenshot ? ' stream-timeline-has-screenshot' : ''}`}
                  >
                    <span className="stream-timeline-dot" aria-hidden />
                    <div className="stream-timeline-body">
                      <div className="stream-timeline-title">{item.message}</div>
                      {item.detail ? (
                        <div className="stream-timeline-detail">{item.detail}</div>
                      ) : null}
                      {item.screenshot ? (
                        <div className="stream-timeline-screenshot-wrap">
                          <img
                            src={`data:image/png;base64,${item.screenshot}`}
                            alt={`Step ${item.step ?? i} screenshot`}
                            className="stream-timeline-screenshot"
                            loading="lazy"
                          />
                          <span className="stream-timeline-screenshot-label">Screenshot used for this step</span>
                        </div>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {liveReply ? (
              <div className="msg-bot-body msg-stream-reply-body">
                <div className="msg-markdown stream-reply-md">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    urlTransform={markdownUrlTransform}
                    components={workspaceEditMarkdownComponents}
                  >
                    {liveReply}
                  </ReactMarkdown>
                </div>
                <CopyResponseButton text={liveReply} />
              </div>
            ) : null}
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>
      <ControlPlane
        liveUsage={liveUsage || { tokens_used: 0, max_tokens: spendLimits.max_tokens_per_run }}
        tasks={controlTasks}
        runs={activeRuns}
        sending={sending}
        onResumeTask={(t) => {
          handleSend({
            text: t.next_action || t.goal || 'Resume the paused task.',
            resumeTaskId: t.id,
          })
        }}
        onCancelTask={async (id) => {
          await cancelTask(id)
          refreshControlPlane()
        }}
        onStopRun={async (id) => {
          await stopActiveRun(id)
          abortRef.current?.abort()
          refreshControlPlane()
        }}
        onSteer={async (id, note) => {
          await steerActiveRun(id, note)
        }}
      />
      {pendingApprovals.length > 0 ? (
        <div className="hitl-stack" role="region" aria-label="Pending approvals">
          {pendingApprovals.map((p) => (
            <div key={p.id} className="hitl-card">
              <div className="hitl-card__head">Needs your approval</div>
              <p className="hitl-card__summary">{p.summary || p.kind}</p>
              <div className="hitl-card__actions">
                <button
                  type="button"
                  className="workspace-file-review__btn workspace-file-review__btn--accent"
                  onClick={async () => {
                    await resolveAgentApproval(p.id, true, currentChatId)
                    refreshPendingApprovals()
                  }}
                >
                  Confirm
                </button>
                <button
                  type="button"
                  className="workspace-file-review__btn workspace-file-review__btn--ghost"
                  onClick={async () => {
                    await resolveAgentApproval(p.id, false, currentChatId)
                    refreshPendingApprovals()
                  }}
                >
                  Deny
                </button>
              </div>
            </div>
          ))}
        </div>
      ) : null}
      <div className="chat-input-area">
        {messageQueue.length > 0 ? (
          <div className="message-queue" aria-label="Queued follow-ups">
            <span className="message-queue__label">
              {queueHeld
                ? `Held (${messageQueue.length}) — will not auto-send`
                : `Queued (${messageQueue.length}) — runs after this turn`}
            </span>
            <button
              type="button"
              className="message-queue__hold"
              onClick={() => {
                const next = !queueHeldRef.current
                queueHeldRef.current = next
                setQueueHeld(next)
                if (!next && !sendingRef.current && messageQueueRef.current.length) {
                  const [first, ...rest] = messageQueueRef.current
                  messageQueueRef.current = rest
                  setMessageQueue(rest)
                  setTimeout(() => handleSend({ text: first, fromQueue: true }), 0)
                }
              }}
            >
              {queueHeld ? 'Resume' : 'Hold'}
            </button>
            {messageQueue.map((t, i) => (
              <button
                key={`${i}-${t.slice(0, 12)}`}
                type="button"
                className="message-queue__item"
                title="Remove from queue"
                onClick={() => {
                  const next = messageQueueRef.current.filter((_, j) => j !== i)
                  messageQueueRef.current = next
                  setMessageQueue(next)
                }}
              >
                {t.slice(0, 80)}
                {t.length > 80 ? '…' : ''}
              </button>
            ))}
          </div>
        ) : null}
        {attachments.length > 0 && (
          <div className="chat-attachments">
            {attachments.map((f, i) => (
              <span key={i} className="chat-attachment">
                <span className="chat-attachment-name" title={f.name}>
                  {f.name}
                </span>
                <button
                  type="button"
                  className="chat-attachment-remove"
                  aria-label="Remove"
                  onClick={() => removeAttachment(i)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="chat-input-row">
          <ModeMenu
            runMode={runMode}
            onRunMode={async (m) => {
              try {
                await setRunMode(m)
                setRunModeState(m)
              } catch (e) {
                alert(e?.message || 'Could not change run mode.')
              }
            }}
          />
          <div className="chat-add-wrap" ref={addMenuRef}>
            <button
              type="button"
              id="chat-add"
              className="chat-add-btn"
              aria-label="Attach files or enable web search"
              aria-expanded={addMenuOpen}
              aria-haspopup="menu"
              onClick={() => setAddMenuOpen((o) => !o)}
            >
              +
            </button>
            {addMenuOpen ? (
              <div className="chat-add-menu" role="menu">
                <button
                  type="button"
                  role="menuitem"
                  className="chat-add-menu-item"
                  onClick={() => {
                    setAddMenuOpen(false)
                    handleAttach()
                  }}
                >
                  {CLIP_MENU_ICON}
                  <span className="chat-add-menu-label">Attach</span>
                  <span className="chat-add-menu-hint">files</span>
                </button>
                <button
                  type="button"
                  role="menuitem"
                  className={`chat-add-menu-item${webSearchMode ? ' chat-add-menu-item--active' : ''}`}
                  aria-pressed={webSearchMode}
                  onClick={toggleWebSearchMode}
                >
                  {GLOBE_MENU_ICON}
                  <span className="chat-add-menu-label">Web search</span>
                  <span className="chat-add-menu-hint">{webSearchMode ? 'on' : 'off'}</span>
                </button>
              </div>
            ) : null}
          </div>
          <input type="file" ref={fileInputRef} style={{ display: 'none' }} multiple onChange={onFileChange} />
          <div className="chat-input-composer">
            {slashMention?.matches?.length ? (
              <div id="chat-slash-mention-list" className="chat-file-mention" role="listbox" aria-label="Slash commands">
                {slashMention.matches.map((row, i) => (
                  <button
                    key={row.name}
                    type="button"
                    role="option"
                    id={`chat-slash-mention-${i}`}
                    aria-selected={i === slashMention.highlight}
                    className={`chat-file-mention-item${i === slashMention.highlight ? ' chat-file-mention-item--active' : ''}`}
                    onMouseEnter={() => setSlashMention((sm) => (sm ? { ...sm, highlight: i } : null))}
                    onMouseDown={(ev) => {
                      ev.preventDefault()
                      applySlashMention(row.name)
                    }}
                  >
                    <span className="chat-file-mention-path">/{row.name}</span>
                    <span className="chat-slash-mention-hint">{row.description || row.argument_hint || ''}</span>
                  </button>
                ))}
              </div>
            ) : null}
            {fileMention && codingModeEnabled && workspaceRelPaths.length > 0 ? (
              <div
                id="chat-file-mention-list"
                className="chat-file-mention"
                role="listbox"
                aria-label="Project files"
              >
                {fileMention.matches.length === 0 ? (
                  <div className="chat-file-mention-empty" role="option">
                    No matching files
                  </div>
                ) : (
                  <>
                    {fileMention.selectedPaths?.length > 0 ? (
                      <div className="chat-file-mention-hint">
                        {fileMention.selectedPaths.length} selected — Enter to insert · Space to toggle row ·
                        Ctrl/⌘-click rows
                      </div>
                    ) : (
                      <div className="chat-file-mention-hint chat-file-mention-hint--subtle">
                        Ctrl/⌘-click to select multiple, Enter to insert · click inserts one
                      </div>
                    )}
                    {fileMention.matches.map((rel, i) => {
                      const picked = fileMention.selectedPaths?.includes(rel)
                      return (
                        <button
                          key={rel}
                          type="button"
                          role="option"
                          id={`chat-file-mention-${i}`}
                          aria-selected={picked || i === fileMention.highlight}
                          className={`chat-file-mention-item${i === fileMention.highlight ? ' chat-file-mention-item--active' : ''}${picked ? ' chat-file-mention-item--picked' : ''}`}
                          onMouseEnter={() =>
                            setFileMention((fm) => (fm ? { ...fm, highlight: i } : null))
                          }
                          onMouseDown={(ev) => {
                            ev.preventDefault()
                            if (ev.ctrlKey || ev.metaKey) {
                              toggleFileMentionSelect(rel)
                            } else {
                              applyFileMention(rel)
                            }
                          }}
                        >
                          {picked ? (
                            <span className="chat-file-mention-check" aria-hidden>
                              ✓
                            </span>
                          ) : (
                            <span className="chat-file-mention-check-spacer" aria-hidden />
                          )}
                          <span className="chat-file-mention-path">{rel}</span>
                        </button>
                      )
                    })}
                  </>
                )}
              </div>
            ) : null}
            <textarea
              ref={chatInputRef}
              id="chat-input"
              placeholder={
                sending
                  ? 'Jarvis is working — Enter or Tab queues a follow-up'
                  : activeAgent
                    ? `Message ${activeAgent.name}…`
                  : codingModeEnabled && workspaceSnapshot.trim()
                    ? 'Message Jarvis… @file — Ctrl+click several, Enter to insert'
                    : webSearchMode
                      ? 'Message Jarvis… (each send uses the web: top results inform the reply)'
                      : 'Message Jarvis…'
              }
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value)
                syncFileMentionFromCaret(e.target.value, e.target.selectionStart, 'input')
              }}
              onSelect={(e) => {
                syncFileMentionFromCaret(e.target.value, e.target.selectionStart, 'select')
              }}
              onClick={(e) => {
                syncFileMentionFromCaret(e.target.value, e.target.selectionStart, 'select')
              }}
              onKeyDown={handleKeyDown}
              autoComplete="off"
              aria-autocomplete={fileMention || slashMention ? 'list' : undefined}
              aria-controls={
                slashMention
                  ? 'chat-slash-mention-list'
                  : fileMention
                    ? 'chat-file-mention-list'
                    : undefined
              }
              aria-expanded={Boolean(
                slashMention?.matches?.length ||
                  (fileMention && codingModeEnabled && workspaceRelPaths.length > 0),
              )}
            />
          </div>
          {sending ? (
            <button
              type="button"
              id="chat-stop"
              className="chat-stop-btn"
              onClick={() => {
                stopEverything().catch(() => {})
              }}
            >
              Stop
            </button>
          ) : (
            <button type="button" id="chat-send" onClick={() => handleSend({})}>
              Send
            </button>
          )}
        </div>
      </div>
    </>
  )

  return (
    <div className="app">
      <header className="app-navbar">
        <div className="navbar-brand">
          <img src="/Jarvis.jpg" alt="" className="navbar-logo" />
          <span className="navbar-title">Jarvis</span>
        </div>
        <div className="navbar-center">
          <button
            type="button"
            className="navbar-new-chat"
            title="New chat"
            aria-label="New chat"
            onClick={async () => {
              try {
                await stopEverything()
                setAgentEditorId(null)
                const chatId = await createNewChat()
                setCurrentChatIdState(chatId)
                setMessages([])
                setPanel('chats')
                await refreshChatList()
              } catch (e) {
                console.error(e)
              }
            }}
          >
            {NAV_NEW_CHAT_ICON}
          </button>
        </div>
        <nav className="navbar-nav" aria-label="Main navigation">
          <div className="navbar-chats-wrap">
            <button
              type="button"
              className={`navbar-link${panel === 'chats' ? ' navbar-link--active' : ''}`}
              onClick={() => setPanel('chats')}
              aria-current={panel === 'chats' ? 'page' : undefined}
              aria-haspopup="true"
            >
              Chats
            </button>
            <div className="navbar-chats-dropdown" role="region" aria-label="Chat history">
              <div className="navbar-chats-dropdown-inner">
                <div className="navbar-chats-dropdown-header">Recent chats</div>
                <input
                  type="search"
                  className="navbar-chats-search"
                  placeholder="Search chats…"
                  value={chatSearchQ}
                  onChange={(e) => {
                    const v = e.target.value
                    setChatSearchQ(v)
                    if (chatSearchTimerRef.current) clearTimeout(chatSearchTimerRef.current)
                    if (!v.trim()) {
                      setChatSearchHits([])
                      return
                    }
                    chatSearchTimerRef.current = setTimeout(async () => {
                      try {
                        const data = await searchChats(v.trim())
                        setChatSearchHits(Array.isArray(data?.hits) ? data.hits : [])
                      } catch {
                        setChatSearchHits([])
                      }
                    }, 250)
                  }}
                  aria-label="Search chats"
                />
                <div className="navbar-chats-list">
                  {chatSearchHits.length > 0
                    ? chatSearchHits.map((chat) => (
                        <div key={chat.id} className="chat-history-item-wrap navbar-chats-item">
                          <button
                            type="button"
                            className="chat-history-item"
                            onClick={() => {
                              selectChat(chat.id)
                              setPanel('chats')
                            }}
                          >
                            {CHAT_ICON}
                            <span className="chat-history-title" title={chat.snippet || chat.title}>
                              {chat.title}
                            </span>
                          </button>
                        </div>
                      ))
                    : null}
                  {chatSearchQ.trim() && chatSearchHits.length === 0 ? (
                    <p className="navbar-chats-empty">No chats match that search.</p>
                  ) : null}
                  {chatSearchQ.trim() ? null : generalChats.length === 0 ? (
                    <p className="navbar-chats-empty">No conversations yet.</p>
                  ) : (
                    generalChats.map((chat) => (
                      <div
                        key={chat.id}
                        className={`chat-history-item-wrap navbar-chats-item ${currentChatId === chat.id ? 'active' : ''}`}
                      >
                        <button
                          type="button"
                          className="chat-history-item"
                          onClick={() => {
                            selectChat(chat.id)
                            setPanel('chats')
                          }}
                        >
                          {CHAT_ICON}
                          <span className="chat-history-title" title={chat.title}>
                            {chat.parent_id ? `↳ ${chat.title}` : chat.title}
                          </span>
                        </button>
                        <button
                          type="button"
                          className="chat-history-delete"
                          aria-label="Delete chat"
                          onClick={async (e) => {
                            e.stopPropagation()
                            if (!confirm('Delete this chat?')) return
                            try {
                              await deleteChat(chat.id)
                              if (currentChatId === chat.id) {
                                setCurrentChatIdState(null)
                                setMessages([])
                              }
                              await refreshChatList()
                            } catch (err) {
                              console.error(err)
                              alert(err?.message || 'Could not delete chat.')
                            }
                          }}
                        >
                          ×
                        </button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
          <div className="navbar-chats-wrap">
            <button
              type="button"
              className={`navbar-link${activeAgentId ? ' navbar-link--active' : ''}`}
              onClick={() => setPanel('chats')}
              aria-haspopup="true"
            >
              Agents
            </button>
            <div className="navbar-chats-dropdown" role="region" aria-label="Custom agents">
              <div className="navbar-chats-dropdown-inner">
                <div className="navbar-chats-dropdown-header navbar-agents-header">
                  <span>Your agents</span>
                  <button
                    type="button"
                    className="navbar-agents-plus"
                    title="Create agent"
                    aria-label="Create agent"
                    onClick={() => {
                      setCreateAgentError('')
                      setCreateAgentOpen(true)
                    }}
                  >
                    {NAV_NEW_CHAT_ICON}
                  </button>
                </div>
                <div className="navbar-chats-list">
                  <div className={`chat-history-item-wrap navbar-chats-item${!activeAgentId ? ' active' : ''}`}>
                    <button
                      type="button"
                      className="chat-history-item"
                      onClick={() => {
                        selectJarvis()
                        setPanel('chats')
                      }}
                    >
                      <span className="navbar-agent-emoji" aria-hidden>
                        A
                      </span>
                      <span className="chat-history-title">Jarvis</span>
                    </button>
                  </div>
                  {visibleAgents.length === 0 ? (
                    <p className="navbar-chats-empty">No custom agents yet. Use + to describe one.</p>
                  ) : (
                    visibleAgents.map((agent) => {
                      const when = nextAgentRunLabel(agent.next_run_at)
                      return (
                        <div
                          key={agent.id}
                          className={`chat-history-item-wrap navbar-chats-item ${activeAgentId === agent.id ? 'active' : ''}`}
                        >
                          <button
                            type="button"
                            className="chat-history-item"
                            onClick={() => {
                              selectCustomAgent(agent)
                              setPanel('chats')
                            }}
                          >
                            <span className="navbar-agent-emoji" aria-hidden>
                              {agent.emoji || '✦'}
                            </span>
                            <span className="chat-history-title" title={agent.title || agent.name}>
                              {agent.pinned ? '📌 ' : ''}
                              {agent.name}
                              {when ? ` · ${when}` : ''}
                            </span>
                          </button>
                          <button
                            type="button"
                            className="chat-history-delete"
                            aria-label={`Configure ${agent.name}`}
                            title="Configure"
                            onClick={(e) => {
                              e.stopPropagation()
                              setAgentEditorId(agent.id)
                              selectCustomAgent(agent)
                            }}
                          >
                            ⚙
                          </button>
                        </div>
                      )
                    })
                  )}
                  {hiddenAgents.length > 0
                    ? hiddenAgents.map((agent) => (
                        <div
                          key={agent.id}
                          className={`chat-history-item-wrap navbar-chats-item ${activeAgentId === agent.id ? 'active' : ''}`}
                        >
                          <button
                            type="button"
                            className="chat-history-item"
                            onClick={() => {
                              selectCustomAgent(agent)
                              setPanel('chats')
                            }}
                          >
                            <span className="navbar-agent-emoji" aria-hidden>
                              {agent.emoji || '✦'}
                            </span>
                            <span className="chat-history-title">{agent.name} (hidden)</span>
                          </button>
                        </div>
                      ))
                    : null}
                </div>
              </div>
            </div>
          </div>
          <button
            type="button"
            className={`navbar-link${panel === 'activity' ? ' navbar-link--active' : ''}`}
            onClick={() => setPanel('activity')}
            aria-current={panel === 'activity' ? 'page' : undefined}
          >
            Activity
          </button>
          <button
            type="button"
            className={`navbar-link${panel === 'settings' ? ' navbar-link--active' : ''}`}
            onClick={() => setPanel('settings')}
            aria-current={panel === 'settings' ? 'page' : undefined}
          >
            Settings
          </button>
        </nav>
      </header>
      <div
        className={`app-body${codingModeEnabled && panel === 'chats' ? ' app-body--coding-chats' : ''}${agentEditorId ? ' app-body--agent-edit' : ''}`}
      >
        {codingModeEnabled ? (
        <>
        <aside
          className="sidebar sidebar--explorer"
          aria-label="Project explorer"
          style={{ width: codingLayoutWidths.explorer, flexShrink: 0 }}
        >
          <div className="sidebar-panel sidebar-panel--explorer">
            <div className="repo-context-card" aria-label="Project context">
              <div className="repo-context-card__head">
                <span className="repo-context-card__pulse" aria-hidden />
                <h2 className="repo-context-card__title">Project context</h2>
              </div>
              <p className="repo-context-card__subtitle">
                Open a folder on this computer. Desktop Jarvis uses the real path (allowlisted); the browser still builds a local index.
              </p>
              <input
                ref={projectFolderInputRef}
                type="file"
                style={{ display: 'none' }}
                onChange={onProjectFolderInputChange}
                {...{ webkitdirectory: true, directory: true, multiple: true }}
              />
              <div className="repo-context-card__body">
                {!(workspaceSnapshot.trim() || workspaceLocalLabel.trim() || workspaceDiskPath || workspaceRelPaths.length) ? (
                  <div className="repo-context-empty">
                    <p className="repo-context-empty__hint">
                      Choose a folder — Jarvis reads files in your browser and sends an index to the model (no path
                      copy-paste needed).
                    </p>
                    <button
                      type="button"
                      className="repo-context-btn repo-context-btn--primary"
                      onClick={() => pickLocalProjectFolder()}
                      disabled={projectImportBusy || sending}
                    >
                      {projectImportBusy ? 'Reading folder…' : 'Open folder'}
                    </button>
                  </div>
                ) : null}
                {workspaceSnapshot.trim() || workspaceLocalLabel.trim() || workspaceDiskPath || workspaceRelPaths.length ? (
                  <div className="repo-context-linked">
                    <div className="repo-context-linked__row" title={workspaceLocalLabel}>
                      <span className="repo-context-linked__icon-wrap" aria-hidden>
                        {REPO_FOLDER_ICON}
                      </span>
                      <div className="repo-context-linked__meta">
                        <span className="repo-context-linked__name">{workspaceDisplayLabel() || 'Project'}</span>
                        <span className="repo-context-linked__path">
                          {workspaceDiskPath
                            ? workspaceDiskPath
                            : 'Index from this browser — folder read locally.'}
                        </span>
                      </div>
                      <div className="repo-context-linked__actions">
                        <button
                          type="button"
                          className="repo-context-icon-btn"
                          title="Restore the latest file checkpoint (SWE turn or last write)"
                          aria-label="Restore checkpoint"
                          disabled={!latestRestorableCheckpoint}
                          onClick={() => latestRestorableCheckpoint && restoreCheckpointById(latestRestorableCheckpoint.id)}
                        >
                          Restore checkpoint
                        </button>
                        <button
                          type="button"
                          className="repo-context-icon-btn"
                          title="Redo a previously undone project file edit"
                          aria-label="Redo project file edit"
                          disabled={!workspaceUndoUi.canRedo}
                          onClick={() => applyWorkspaceRedo()}
                        >
                          Redo
                        </button>
                        <button
                          type="button"
                          className="repo-context-icon-btn"
                          title="Pick another folder"
                          aria-label="Pick another folder"
                          onClick={() => pickLocalProjectFolder()}
                          disabled={projectImportBusy}
                        >
                          Change
                        </button>
                        <button
                          type="button"
                          className="repo-context-icon-btn"
                          title="Remove project"
                          aria-label="Remove project"
                          onClick={clearProjectContext}
                        >
                          Clear
                        </button>
                      </div>
                    </div>
                    {workspaceRelPaths.length > 0 ? (
                      <ProjectFileTree
                        rootLabel={workspaceLocalLabel || 'Project'}
                        relPaths={workspaceRelPaths}
                        onFileOpen={openProjectFile}
                        activePath={filePreview?.relPath || ''}
                      />
                    ) : (
                      <div className="repo-context-tree-empty">
                        <p>{projectImportBusy ? 'Reading folder…' : 'Loading the file tree…'}</p>
                        {!projectImportBusy ? (
                          <button type="button" className="repo-context-btn repo-context-btn--ghost" onClick={reloadWorkspaceTree}>
                            Load files
                          </button>
                        ) : null}
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            </div>
          </div>
        </aside>
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize project explorer"
          title="Drag to resize explorer"
          tabIndex={0}
          className="panel-resizer"
          onPointerDown={onExplorerPanelResizePointerDown}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') {
              e.preventDefault()
              nudgeExplorerPanel(-16)
            } else if (e.key === 'ArrowRight') {
              e.preventDefault()
              nudgeExplorerPanel(16)
            }
          }}
        />
        </>
        ) : null}
        {codingModeEnabled && panel === 'chats' ? (
          <div className="main-column main-column--coding-ide">
            <div className="coding-workbench">
              {visibleWorkspaceEditsSession ? (
                <WorkspaceFileReview
                  variant="workbench"
                  session={visibleWorkspaceEditsSession}
                  workspaceLabel={workspaceDisplayLabel() || ''}
                  resolveBaseContent={resolveWorkspaceFileBase}
                  onWriteFile={saveProjectFile}
                  onDismiss={() => {
                    setPendingWorkspaceEdits(null)
                    setWorkspaceReviewHiddenPaths(new Set())
                  }}
                  onOpenFile={openProjectFile}
                  activeFileIndex={workspaceReviewFileIndex}
                  onActiveFileIndexChange={setWorkspaceReviewFileIndex}
                  onFileRemovedFromQueue={(p) =>
                    setWorkspaceReviewHiddenPaths((s) => new Set(s).add(p))
                  }
                />
              ) : filePreview ? (
                <ChatFilePreview
                  preview={filePreview}
                  onClose={() => setFilePreview(null)}
                  onSave={saveProjectFile}
                  onRun={runOpenWorkspaceFile}
                  colorScheme={colorScheme}
                />
              ) : (
                <div className="coding-workbench-empty">
                  <div className="coding-workbench-empty__inner">
                    <h2 className="coding-workbench-empty__title">Coding workspace</h2>
                    <p className="coding-workbench-empty__text">
                      Open a file from the tree to preview it here, or send a message so Jarvis can propose edits—diffs
                      appear in this pane for review.
                    </p>
                  </div>
                </div>
              )}
            </div>
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label="Resize chat panel"
              title="Drag to resize chat"
              tabIndex={0}
              className="panel-resizer"
              onPointerDown={onChatRailResizePointerDown}
              onKeyDown={(e) => {
                if (e.key === 'ArrowLeft') {
                  e.preventDefault()
                  nudgeChatRailPanel(16)
                } else if (e.key === 'ArrowRight') {
                  e.preventDefault()
                  nudgeChatRailPanel(-16)
                }
              }}
            />
            <div
              className="coding-chat-rail"
              style={{ width: codingLayoutWidths.chatRail, flexShrink: 0 }}
            >
              <header className="chat-rail-header">
                <div className="chat-rail-header__titles">
                  <span className="chat-rail-header__kicker">Coding mode</span>
                  <span className="chat-rail-header__project" title={workspaceDisplayLabel() || ''}>
                    {workspaceSnapshot.trim() ? workspaceDisplayLabel() || 'Project linked' : 'No folder linked'}
                  </span>
                </div>
              </header>
              {visibleWorkspaceEditsSession ? (
                <CodingEditSummaryCards
                  session={visibleWorkspaceEditsSession}
                  resolveBaseContent={resolveWorkspaceFileBase}
                  activePath={visibleWorkspaceEditsSession.files[workspaceReviewFileIndex]?.path}
                  onSelectPath={selectWorkspaceEditFile}
                />
              ) : null}
              <div className="chat-container chat-container--rail">{chatMainInner}</div>
              <ChatTerminalPanel ref={terminalRef} />
              <footer className="app-context-footer app-context-footer--chat-rail" role="status">
                <div className="app-context-footer__cluster">
                  {workspaceSnapshot.trim() ? (
                    <>
                      <span className="app-context-footer__badge" title={workspaceDisplayLabel()}>
                        {REPO_FOLDER_ICON}
                        <span className="app-context-footer__badge-text">{workspaceDisplayLabel()}</span>
                      </span>
                      <span className="app-context-footer__path" title="Browser-built index">
                        Local folder (browser index)
                      </span>
                    </>
                  ) : (
                    <span className="app-context-footer__idle">No project linked</span>
                  )}
                </div>
                <div className="app-context-footer__actions">
                  {usageStats ? (
                    <span
                      className="app-context-footer__usage"
                      title="Estimated tokens from recent traces"
                    >
                      Tokens ~ {usageStats.token_input || 0} in / {usageStats.token_output || 0} out
                    </span>
                  ) : null}
                  <button
                    type="button"
                    className="app-context-footer__linkish"
                    onClick={() => pickLocalProjectFolder()}
                    disabled={projectImportBusy || sending}
                  >
                    {projectImportBusy ? 'Reading…' : 'Open folder'}
                  </button>
                </div>
              </footer>
            </div>
          </div>
        ) : (
          <div className="main-column">
        {panel === 'chats' ? (
        <div className={`chat-container${filePreview ? ' chat-container--file-preview-open' : ''}`}>
          <ChatFilePreview
            preview={filePreview}
            onClose={() => setFilePreview(null)}
            onSave={codingModeEnabled ? saveProjectFile : null}
            onRun={runOpenWorkspaceFile}
            colorScheme={colorScheme}
          />
          {chatMainInner}
          <ChatTerminalPanel ref={terminalRef} />
        </div>
        ) : null}
        {panel === 'activity' ? (
          <div className="main-panel main-panel--activity">
            <div className="main-panel-inner activity-list">
              {messageQueue.length ? (
                <div className="activity-block">
                  <h3 className="activity-heading">Queued follow-ups</h3>
                  {messageQueue.map((t, i) => (
                    <p key={i} className="activity-line">{t}</p>
                  ))}
                </div>
              ) : null}
              {pendingApprovals.length ? (
                <div className="activity-block">
                  <h3 className="activity-heading">Awaiting approval</h3>
                  {pendingApprovals.map((p) => (
                    <p key={p.id} className="activity-line">{p.summary || p.kind}</p>
                  ))}
                </div>
              ) : null}
              <div className="activity-block">
                <h3 className="activity-heading">Pinned replies</h3>
                {bookmarks.length === 0 ? (
                  <p className="activity-empty">Pin an assistant reply from chat to save it here.</p>
                ) : (
                  bookmarks.map((b) => (
                    <div key={b.id} className="activity-pin">
                      <button
                        type="button"
                        className="activity-pin__open"
                        onClick={() => {
                          if (b.chat_id) selectChat(b.chat_id)
                          setPanel('chats')
                        }}
                      >
                        {b.title || 'Pin'}
                      </button>
                      <button
                        type="button"
                        className="activity-pin__del"
                        onClick={async () => {
                          await deleteBookmark(b.id)
                          refreshBookmarks()
                        }}
                      >
                        ×
                      </button>
                    </div>
                  ))
                )}
              </div>
              {usageStats?.last?.route ? (
                <div className="activity-block">
                  <h3 className="activity-heading">Last run</h3>
                  <p className="activity-line">
                    {usageStats.last.route} · in {usageStats.last.token_input ?? '—'} / out{' '}
                    {usageStats.last.token_output ?? '—'} tokens
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
        {panel === 'settings' ? (
          <div className="main-panel main-panel--settings">
            <div className="main-panel-inner settings-panel">
              <div className="settings-section">
                <label className="settings-label">Appearance</label>
                <p className="settings-description">Interface color scheme.</p>
                <div className="settings-theme-switch" role="group" aria-label="Color scheme">
                  <button
                    type="button"
                    className={`settings-theme-option${colorScheme === 'dark' ? ' settings-theme-option--active' : ''}`}
                    onClick={() => setColorScheme('dark')}
                    aria-pressed={colorScheme === 'dark'}
                  >
                    Dark
                  </button>
                  <button
                    type="button"
                    className={`settings-theme-option${colorScheme === 'light' ? ' settings-theme-option--active' : ''}`}
                    onClick={() => setColorScheme('light')}
                    aria-pressed={colorScheme === 'light'}
                  >
                    Light
                  </button>
                </div>
              </div>
              <div className="settings-section">
                <label className="settings-label">Coding mode</label>
                <p className="settings-description">
                  When enabled, the project explorer is shown and you can open a folder so each message includes a text
                  snapshot of that project. The coding agent uses it to propose real file updates: you get a diff review
                  panel and can apply changes to the linked folder (or clear rejects per hunk). When disabled, chat runs
                  without project context.
                </p>
                <div className="settings-theme-switch" role="group" aria-label="Coding mode">
                  <button
                    type="button"
                    className={`settings-theme-option${codingModeEnabled ? ' settings-theme-option--active' : ''}`}
                    onClick={() => setCodingModeEnabled(true)}
                    aria-pressed={codingModeEnabled}
                  >
                    On
                  </button>
                  <button
                    type="button"
                    className={`settings-theme-option${!codingModeEnabled ? ' settings-theme-option--active' : ''}`}
                    onClick={() => setCodingModeEnabled(false)}
                    aria-pressed={!codingModeEnabled}
                  >
                    Off
                  </button>
                </div>
              </div>
              <div className="settings-section">
                <label className="settings-label">Storage location</label>
                <p className="settings-description">Where chat logs are saved.</p>
                <div className="settings-storage-row">
                  <input
                    type="text"
                    readOnly
                    className="settings-storage-input"
                    value={storagePath}
                    aria-label="Chats storage path"
                  />
                  <button type="button" className="settings-storage-btn" onClick={handleStorageChange}>
                    Change
                  </button>
                </div>
              </div>
              <div className="settings-section">
                <label className="settings-label">Model</label>
                <p className="settings-description">
                  Jarvis probes GPU, NPU, and RAM on this PC at launch (Windows, macOS, Linux) and picks a local model that fits. Cloud APIs stay optional.
                </p>
                <select
                  className="settings-model-select"
                  value={modelProvider === 'local' && localModelId ? `local:${localModelId}` : modelProvider}
                  onChange={handleModelChange}
                  aria-label="Model provider"
                >
                  <option value="openai">OpenAI (GPT)</option>
                  <option value="xai">xAI (Grok)</option>
                  <optgroup label="Local (Hugging Face)">
                    {(localModelsInfo?.catalog || []).map((m) => (
                      <option key={m.id} value={`local:${m.id}`}>
                        {m.name}
                        {m.recommended ? ' — suggested' : ''}
                        {m.installed ? ' ✓' : ''}
                        {!m.fits ? ' (large for this machine)' : ''}
                      </option>
                    ))}
                  </optgroup>
                </select>
                {localModelsInfo ? (
                  <div className="settings-hw">
                    <p className="settings-hw__summary">
                      {localModelsInfo.hardware?.has_gpu ? 'GPU detected' : 'No dedicated GPU'}
                      {localModelsInfo.hardware?.has_npu ? ' · NPU detected' : ''}
                      {localModelsInfo.hardware?.system_ram_gb
                        ? ` · ${localModelsInfo.hardware.system_ram_gb} GB RAM`
                        : ''}
                      {localModelsInfo.hardware?.usable_memory_gb
                        ? ` · ~${localModelsInfo.hardware.usable_memory_gb} GB usable for weights`
                        : ''}
                    </p>
                    <ul className="settings-hw__list">
                      {(localModelsInfo.devices || []).map((d, i) => (
                        <li key={`${d.kind}-${d.name}-${i}`}>
                          <strong>{(d.kind || 'device').toUpperCase()}</strong> {d.name}
                          {d.memory_gb != null
                            ? ` · ${d.memory_gb} GB`
                            : d.memory_gb_estimate != null
                              ? ` · ~${d.memory_gb_estimate} GB shared`
                              : ' · shared memory'}
                          {d.suggested_name ? ` → ${d.suggested_name}` : ''}
                        </li>
                      ))}
                    </ul>
                    <p className="settings-description">
                      Suggested: <strong>{localModelsInfo.suggested_name}</strong>
                      {localModelsInfo.backends?.llama_cpp || localModelsInfo.backends?.llama_server
                        ? ' · local llama.cpp runtime ready'
                        : localModelsInfo.backends?.transformers
                          ? ' · transformers ready'
                          : ' · first download also fetches a llama.cpp binary for this OS'}
                    </p>
                    {(localJob?.status === 'downloading' || localJob?.status === 'loading') && (
                      <div className="settings-hw__job">
                        <div className="settings-hw__bar" aria-hidden>
                          <span style={{ width: `${Math.max(4, localJob.progress || 0)}%` }} />
                        </div>
                        <p className="settings-description">
                          {localJob.message || localJob.status} ({localJob.progress || 0}%)
                        </p>
                      </div>
                    )}
                    {localJob?.status === 'error' ? (
                      <p className="settings-description settings-hw__err">{localJob.error || localJob.message}</p>
                    ) : null}
                    <div className="settings-hw__actions">
                      <button
                        type="button"
                        className="settings-storage-btn"
                        disabled={localJob?.status === 'downloading' || localJob?.status === 'loading'}
                        onClick={async () => {
                          const mid = localModelsInfo.suggested_model_id
                          if (!mid) return
                          try {
                            await downloadLocalModel(mid)
                            setLocalJob({
                              status: 'downloading',
                              progress: 1,
                              model_id: mid,
                              message: 'Starting download…',
                            })
                          } catch (err) {
                            alert(err?.message || 'Download failed.')
                          }
                        }}
                      >
                        Download suggested model
                      </button>
                    </div>
                  </div>
                ) : (
                  <p className="settings-description">Detecting GPU/NPU…</p>
                )}
              </div>
              <div className="settings-section">
                <label className="settings-label">API keys</label>
                <p className="settings-description">
                  Stored in your user data folder, never in the installer.
                  {keysStatus.openai_set ? ' OpenAI is set.' : ' OpenAI is not set.'}
                  {keysStatus.xai_set ? ' xAI is set.' : ' xAI is not set.'}
                </p>
                <input
                  type="password"
                  className="settings-storage-input"
                  placeholder="OpenAI API key"
                  value={openaiKeyDraft}
                  onChange={(e) => setOpenaiKeyDraft(e.target.value)}
                  autoComplete="off"
                />
                <input
                  type="password"
                  className="settings-storage-input"
                  placeholder="xAI API key"
                  value={xaiKeyDraft}
                  onChange={(e) => setXaiKeyDraft(e.target.value)}
                  autoComplete="off"
                  style={{ marginTop: '0.4rem' }}
                />
                <button
                  type="button"
                  className="settings-storage-btn"
                  style={{ marginTop: '0.5rem' }}
                  onClick={async () => {
                    try {
                      const st = await saveApiKeys({
                        openaiApiKey: openaiKeyDraft || undefined,
                        xaiApiKey: xaiKeyDraft || undefined,
                      })
                      setKeysStatus(st)
                      setOpenaiKeyDraft('')
                      setXaiKeyDraft('')
                    } catch (e) {
                      alert(e?.message || 'Could not save keys.')
                    }
                  }}
                >
                  Save keys
                </button>
              </div>
              <div className="settings-section">
                <label className="settings-label">Autonomy</label>
                <p className="settings-description">
                  How much Jarvis may execute without asking when the composer is on Agent. Switch Plan / Draft / Agent next to the chat box. High-impact writes (email, delete, shell, GUI) stay gated by default.
                </p>
                <select
                  className="settings-model-select"
                  value={autonomyLevel}
                  onChange={async (e) => {
                    const v = e.target.value
                    try {
                      await setAutonomyLevel(v)
                      setAutonomyLevelState(v)
                    } catch (err) {
                      alert(err?.message || 'Could not save autonomy.')
                    }
                  }}
                  aria-label="Autonomy level"
                >
                  <option value="recommend">Recommend only</option>
                  <option value="draft">Draft, you execute</option>
                  <option value="low_risk_auto">Low-risk auto (reads)</option>
                  <option value="gated">Gated writes (recommended)</option>
                  <option value="limited_auto">Limited auto</option>
                </select>
              </div>
              <div className="settings-section">
                <label className="settings-label">Spend cap (tokens / run)</label>
                <p className="settings-description">
                  Hard stop when a single run exceeds this estimate. Live meter is above the composer.
                </p>
                <input
                  className="settings-model-select"
                  type="number"
                  min={1000}
                  step={1000}
                  value={spendLimits.max_tokens_per_run}
                  onChange={(e) =>
                    setSpendLimitsState({
                      ...spendLimits,
                      max_tokens_per_run: Number(e.target.value) || 80000,
                    })
                  }
                  onBlur={async () => {
                    try {
                      const next = await setSpendLimits(spendLimits.max_tokens_per_run, spendLimits.warn_tokens)
                      setSpendLimitsState(next)
                    } catch (err) {
                      alert(err?.message || 'Could not save spend cap.')
                    }
                  }}
                />
              </div>
              <div className="settings-section">
                <label className="settings-label">Quiet hours</label>
                <p className="settings-description">
                  Scheduled agent routines wait until this window ends (unless the routine opts out).
                </p>
                <label className="settings-check">
                  <input
                    type="checkbox"
                    checked={!!quietHours.enabled}
                    onChange={async (e) => {
                      const next = { ...quietHours, enabled: e.target.checked }
                      setQuietHoursState(next)
                      await setQuietHours(next)
                    }}
                  />
                  Enabled
                </label>
                <div className="settings-quiet-row">
                  <input
                    type="time"
                    value={quietHours.start}
                    onChange={(e) => setQuietHoursState({ ...quietHours, start: e.target.value })}
                    onBlur={() => setQuietHours(quietHours)}
                  />
                  <span>to</span>
                  <input
                    type="time"
                    value={quietHours.end}
                    onChange={(e) => setQuietHoursState({ ...quietHours, end: e.target.value })}
                    onBlur={() => setQuietHours(quietHours)}
                  />
                </div>
              </div>
              <div className="settings-section">
                <label className="settings-label">Identity + facts</label>
                <p className="settings-description">
                  Always-loaded SOUL / USER / MEMORY files, plus decaying exact facts. Say <code>remember …</code> in chat to add a fact.
                </p>
                <textarea
                  className="settings-identity"
                  rows={4}
                  value={identity.user || ''}
                  onChange={(e) => setIdentity({ ...identity, user: e.target.value })}
                  onBlur={() => saveIdentity({ user: identity.user })}
                  placeholder="USER.md"
                />
                <div className="settings-fact-add">
                  <input
                    value={factDraft}
                    onChange={(e) => setFactDraft(e.target.value)}
                    placeholder="Add a fact"
                  />
                  <button
                    type="button"
                    className="settings-storage-btn"
                    onClick={async () => {
                      if (!factDraft.trim()) return
                      await addFact(factDraft.trim())
                      setFactDraft('')
                      refreshMemoryExtras()
                    }}
                  >
                    Add
                  </button>
                </div>
                <ul className="settings-fact-list">
                  {facts.slice(0, 8).map((f) => (
                    <li key={f.id}>
                      <span>{f.text}</span>
                      <button type="button" onClick={async () => { await deleteFact(f.id); refreshMemoryExtras() }}>
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
              <div className="settings-section">
                <label className="settings-label">Checkpoints</label>
                <p className="settings-description">
                  File writes and SWE applies are snapshotted. Restore a checkpoint to put those files back.
                </p>
                <ul className="settings-fact-list">
                  {checkpoints.slice(0, 12).map((c) => {
                    const restorable = c.kind === 'workspace_write' || c.kind === 'overlay_turn'
                    return (
                    <li key={c.id}>
                      <span>{c.kind}: {c.summary}</span>
                      {restorable ? (
                        <button
                          type="button"
                          onClick={() => restoreCheckpointById(c.id)}
                        >
                          Restore checkpoint
                        </button>
                      ) : null}
                    </li>
                    )
                  })}
                </ul>
              </div>
              <div className="settings-section">
                <label className="settings-label">Desktop GUI control</label>
                <p className="settings-description">
                  The desktop agent can move the mouse and type. It stays off until you arm it for this machine.
                </p>
                <div className="settings-theme-switch" role="group" aria-label="Desktop armed">
                  <button
                    type="button"
                    className={`settings-theme-option${desktopArmed ? ' settings-theme-option--active' : ''}`}
                    onClick={async () => {
                      await setDesktopArmed(true)
                      setDesktopArmedState(true)
                    }}
                    aria-pressed={desktopArmed}
                  >
                    Armed
                  </button>
                  <button
                    type="button"
                    className={`settings-theme-option${!desktopArmed ? ' settings-theme-option--active' : ''}`}
                    onClick={async () => {
                      await setDesktopArmed(false)
                      setDesktopArmedState(false)
                    }}
                    aria-pressed={!desktopArmed}
                  >
                    Off
                  </button>
                </div>
              </div>
              <div className="settings-section">
                <label className="settings-label">Google Calendar + Gmail</label>
                {googleAuth.connected ? (
                  <div className="settings-auth-connected settings-auth-connected--col">
                    <div className="settings-auth-row">
                      <span className="settings-auth-pill">Connected</span>
                      <span className="settings-auth-user">
                        {googleAuth.user?.email || googleAuth.user?.name || 'Google account'}
                      </span>
                    </div>
                    {gmailStatus?.ok ? (
                      <div className="settings-gmail-line">
                        Gmail · {gmailStatus.emailAddress || 'linked'}
                        {gmailStatus.messagesTotal != null ? ` · ${gmailStatus.messagesTotal} messages` : ''}
                      </div>
                    ) : gmailStatus && !gmailStatus.ok ? (
                      <div className="settings-gmail-line settings-gmail-line--warn">{gmailStatus.error}</div>
                    ) : null}
                  </div>
                ) : (
                  <div className="settings-auth-connected">
                    <span className="settings-auth-pill settings-auth-pill--idle">
                      {googleAuth.configured ? 'Not connected' : 'Unavailable'}
                    </span>
                  </div>
                )}
                <div className="settings-auth-actions">
                  {!googleAuth.connected ? (
                    <button
                      type="button"
                      className="settings-storage-btn"
                      disabled={!googleAuth.configured}
                      title={!googleAuth.configured ? 'OAuth is not configured on the server' : undefined}
                      onClick={() => {
                        window.location.href = getGoogleAuthLoginUrl('/settings')
                      }}
                    >
                      Sign in with Google
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="settings-storage-btn"
                        disabled={googleAuthBusy}
                        onClick={async () => {
                          setGoogleAuthBusy(true)
                          try {
                            await googleLogout()
                            await refreshGoogleAuth()
                          } finally {
                            setGoogleAuthBusy(false)
                          }
                        }}
                      >
                        Log out
                      </button>
                      <button
                        type="button"
                        className="settings-storage-btn"
                        disabled={googleAuthBusy}
                        onClick={async () => {
                          if (!confirm('Disconnect Google account and revoke stored access?')) return
                          setGoogleAuthBusy(true)
                          try {
                            await googleDisconnect()
                            await refreshGoogleAuth()
                          } finally {
                            setGoogleAuthBusy(false)
                          }
                        }}
                      >
                        Disconnect
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          </div>
        ) : null}
        <footer className="app-context-footer" role="status">
          <div className="app-context-footer__cluster">
            {!codingModeEnabled ? (
              <span className="app-context-footer__idle">Coding mode is off — turn it on in Settings to use a project.</span>
            ) : workspaceSnapshot.trim() ? (
              <>
                <span className="app-context-footer__badge" title={workspaceDisplayLabel()}>
                  {REPO_FOLDER_ICON}
                  <span className="app-context-footer__badge-text">{workspaceDisplayLabel()}</span>
                </span>
                <span className="app-context-footer__path" title={workspaceDiskPath || 'Browser-built index'}>
                  {workspaceDiskPath ? 'Linked folder' : 'Local folder (browser index)'}
                </span>
              </>
            ) : (
              <span className="app-context-footer__idle">No project linked</span>
            )}
          </div>
          <div className="app-context-footer__actions">
            {usageStats ? (
              <span className="app-context-footer__usage" title="Estimated tokens from recent traces">
                Tokens ~ {usageStats.token_input || 0} in / {usageStats.token_output || 0} out
              </span>
            ) : null}
            {codingModeEnabled ? (
              <button
                type="button"
                className="app-context-footer__linkish"
                onClick={() => pickLocalProjectFolder()}
                disabled={projectImportBusy || sending}
              >
                {projectImportBusy ? 'Reading…' : 'Open folder'}
              </button>
            ) : null}
          </div>
        </footer>
        </div>
        )}
        {agentEditorId ? (
          <CustomAgentEditor
            agentId={agentEditorId}
            onClose={() => setAgentEditorId(null)}
            onChanged={async () => {
              await refreshCustomAgents()
              if (currentChatId) {
                try {
                  const msgs = await readChatLog(currentChatId)
                  setMessages(msgs || [])
                } catch {
                  /* ignore */
                }
              }
            }}
            onDeleted={async () => {
              setAgentEditorId(null)
              await refreshCustomAgents()
              const chatId = await createNewChat()
              setCurrentChatIdState(chatId)
              setMessages([])
              await refreshChatList()
            }}
          />
        ) : null}
      </div>
      <CreateAgentModal
        open={createAgentOpen}
        busy={creatingAgent}
        error={createAgentError}
        onClose={() => {
          if (!creatingAgent) {
            setCreateAgentOpen(false)
            setCreateAgentError('')
          }
        }}
        onSubmit={handleCreateAgentFromBrief}
      />
    </div>
  )
}

export default App
