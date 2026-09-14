function detectDesktopShell() {
  if (typeof window === 'undefined') return false
  return Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__)
}

export function isDesktopShell() {
  return detectDesktopShell()
}

/** HTTP API origin. Vite proxy `/api` in the browser; Tauri talks to the localhost sidecar. */
export function getApiBase() {
  const env = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '')
  if (env) return env
  if (detectDesktopShell()) return 'http://127.0.0.1:8000'
  return '/api'
}

const API_BASE = getApiBase()

async function request(path, options = {}) {
  const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
  const res = await fetch(url, {
    credentials: 'include',
    ...options,
    headers: { 'Content-Type': 'application/json', ...options.headers },
  });
  if (!res.ok) {
    const text = await res.text();
    // Vite proxy returns 502 when nothing listens on localhost:8000
    if (res.status === 502) {
      throw new Error(
        'Cannot reach the API. Start the backend from the repo: cd backend && poetry run uvicorn main:app --reload --port 8000'
      );
    }
    throw new Error(text || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function chatbotResponse(message, attachmentPaths = null, webSearchQuery = null) {
  const { reply } = await request('/chat/response', {
    method: 'POST',
    body: JSON.stringify({
      message: message || '',
      attachment_paths: attachmentPaths,
      web_search_query: webSearchQuery || null,
    }),
  });
  return reply;
}

export async function sendMessage(
  message,
  attachmentPaths = null,
  chatId = null,
  webSearchQuery = null,
  codingMode = false,
  codingProjectSnapshot = null,
) {
  const { reply } = await request('/chat/send-message', {
    method: 'POST',
    body: JSON.stringify({
      message: message || '',
      attachment_paths: attachmentPaths,
      chat_id: chatId,
      web_search_query: webSearchQuery || null,
      coding_mode: !!codingMode,
      coding_project_snapshot: codingProjectSnapshot || null,
    }),
  });
  return reply;
}

/**
 * Stream send-message: calls onChunk(delta) as tokens arrive, then onDone(fullReply).
 * onStatus({ phase, message, ... }) for supervisor / context / agent progress.
 * onAgentStep({ step, thought, action, description, result, done }) for each agent step (SSE omits screenshot; UI merges WS payloads into the same timeline row).
 * If the backend used a tool, calls onToolUsed(toolUsed) and returns { reply, tool_used }.
 */
export async function sendMessageStream(
  message,
  attachmentPaths,
  chatId,
  {
    onChunk,
    onDone,
    onToolUsed,
    onStatus,
    onAgentStep,
    webSearchQuery = null,
    codingMode = false,
    codingProjectSnapshot = null,
    signal = null,
  },
) {
  const base = getApiBase()
  const res = await fetch(base + '/chat/send-message/stream', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    signal,
    body: JSON.stringify({
      message: message || '',
      attachment_paths: attachmentPaths || null,
      chat_id: chatId || null,
      web_search_query: webSearchQuery || null,
      coding_mode: !!codingMode,
      coding_project_snapshot: codingProjectSnapshot || null,
    }),
  })
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`)
  const reader = res.body.getReader()
  if (signal) {
    const cancelReader = () => {
      reader.cancel().catch(() => {})
    }
    if (signal.aborted) cancelReader()
    else signal.addEventListener('abort', cancelReader, { once: true })
  }
  const decoder = new TextDecoder()
  let buffer = ''
  let full = ''
  let toolUsed = null
  try {
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''
    for (const line of lines) {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6))
          if (data.type === 'status') {
            onStatus?.(data)
            continue
          }
          if (data.type === 'agent_step') {
            onAgentStep?.(data)
            continue
          }
          if (data.delta != null) {
            full += data.delta
            onChunk?.(data.delta)
          }
          if (data.done && data.reply != null) {
            full = data.reply
            if (data.tool_used) {
              toolUsed = data.tool_used
              onToolUsed?.(data.tool_used)
            }
            onDone?.(data.reply, data.file_edits ?? null)
            return {
              reply: data.reply,
              tool_used: data.tool_used ?? null,
              file_edits: data.file_edits ?? null,
              pending_approvals: data.pending_approvals ?? null,
              run_id: data.run_id ?? null,
            }
          }
        } catch (_) {}
      }
    }
  }
  if (full) onDone?.(full)
  return { reply: full, tool_used: toolUsed, file_edits: null, pending_approvals: null }
  } catch (err) {
    if (err?.name === 'AbortError' || signal?.aborted) {
      const abortErr = err?.name === 'AbortError' ? err : new DOMException('Aborted', 'AbortError')
      throw abortErr
    }
    throw err
  }
}

/** Send message with file uploads (multipart). Use when user attached files. */
export async function sendMessageWithFiles(
  message,
  files = [],
  chatId = null,
  webSearchQuery = null,
  codingMode = false,
  codingProjectSnapshot = null,
) {
  const form = new FormData();
  form.append('message', message || '');
  form.append('chat_id', chatId ?? '');
  if (webSearchQuery) form.append('web_search_query', webSearchQuery);
  if (codingMode) form.append('coding_mode', 'true');
  if (codingProjectSnapshot) form.append('coding_project_snapshot', codingProjectSnapshot);
  for (const f of files) {
    form.append('files', f);
  }
  const base = getApiBase();
  const url = base + '/chat/send-message-with-files';
  const res = await fetch(url, { method: 'POST', body: form, credentials: 'include' });
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`);
  const data = await res.json();
  return data.reply;
}

export async function appendChatLog(role, content) {
  await request('/chat/append', {
    method: 'POST',
    body: JSON.stringify({ role, content }),
  });
}

export async function listChats() {
  return request('/chat/list');
}

export async function setCurrentChat(chatId) {
  await request('/chat/set-current', {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId }),
  });
}

/** Create a new empty chat and set it as current. Returns the new chat_id. */
export async function createNewChat() {
  const { chat_id } = await request('/chat/new', { method: 'POST' });
  return chat_id;
}

/** Delete a chat by id. Returns { ok, deleted }. */
export async function deleteChat(chatId) {
  return request(`/chat/${encodeURIComponent(chatId)}`, { method: 'DELETE' });
}

export async function getCurrentChatId() {
  const { chat_id } = await request('/chat/current-id');
  return chat_id;
}

export async function readChatLog(chatId) {
  return request(`/chat/read/${chatId}`);
}

export async function getChatsStoragePath() {
  const { path } = await request('/storage/chats-path');
  return path || '';
}

export async function setChatsStoragePath(path) {
  await request('/storage/chats-path', {
    method: 'POST',
    body: JSON.stringify({ path }),
  });
}

/** Current LLM provider: "openai" (GPT) or "xai" (Grok). */
export async function getModelSetting() {
  const { provider } = await request('/settings/model');
  return provider || 'openai';
}

export async function setModelSetting(provider) {
  await request('/settings/model', {
    method: 'POST',
    body: JSON.stringify({ provider: provider === 'xai' ? 'xai' : 'openai' }),
  });
  return provider;
}

/** WebSocket URL for agent steps (use wsOrigin for WS) */
export function agentStepsWsUrl() {
  const base = getApiBase() === '/api' ? (import.meta.env.VITE_API_URL || '') : getApiBase();
  if (base.startsWith('http://')) {
    return base.replace('http://', 'ws://') + '/ws/agent-steps';
  }
  if (base.startsWith('https://')) {
    return base.replace('https://', 'wss://') + '/ws/agent-steps';
  }
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  return `${proto}//${host}/ws/agent-steps`;
}

export async function getGoogleAuthStatus() {
  return request('/auth/google/status');
}

/** Gmail profile for current session; does not throw on HTTP errors (returns { ok: false, error }). */
export async function getGmailProfile() {
  const base = getApiBase();
  const res = await fetch(`${base}/integrations/gmail/profile`, { credentials: 'include' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { ok: false, error: data.error || `HTTP ${res.status}`, detail: data.detail };
  }
  return data;
}

export function getGoogleAuthLoginUrl(nextPath = '/settings') {
  const base = getApiBase()
  const path = nextPath && nextPath.startsWith('/') ? nextPath : '/settings'
  return `${base}/auth/google/login?next=${encodeURIComponent(path)}`
}

export async function googleLogout() {
  return request('/auth/google/logout', { method: 'POST' });
}

export async function googleDisconnect() {
  return request('/auth/google/disconnect', { method: 'POST' });
}

/** @returns {Promise<object>} User profile (see `backend/memory/user_profile.json`). */
export async function getUserProfile() {
  return request('/memory/user-profile', { method: 'GET' });
}

/** @param {object} profile — full profile object */
export async function saveUserProfile(profile) {
  return request('/memory/user-profile', {
    method: 'PUT',
    body: JSON.stringify(profile),
  });
}

/**
 * Run one command on the API host (shell on by default; Windows defaults to PowerShell).
 * @param {string} command
 * @param {number} [timeoutSec]
 * @returns {Promise<{ ok: boolean, returncode?: number, stdout?: string, stderr?: string, error?: string, shell?: string|null }>}
 */
export async function runHostShellCommand(command, timeoutSec = 120) {
  return request('/tools/shell', {
    method: 'POST',
    body: JSON.stringify({
      command: command || '',
      timeout_sec: timeoutSec,
    }),
  })
}

export async function getRuntimeSettings() {
  return request('/settings/runtime')
}

export async function setAutonomyLevel(autonomy) {
  return request('/settings/autonomy', {
    method: 'POST',
    body: JSON.stringify({ autonomy }),
  })
}

export async function setDesktopArmed(armed) {
  return request('/settings/desktop-armed', {
    method: 'POST',
    body: JSON.stringify({ armed: !!armed }),
  })
}

export async function getApiKeysStatus() {
  return request('/settings/keys-status')
}

export async function saveApiKeys({ openaiApiKey, xaiApiKey }) {
  const body = {}
  if (openaiApiKey != null && openaiApiKey !== '') body.openai_api_key = openaiApiKey
  if (xaiApiKey != null && xaiApiKey !== '') body.xai_api_key = xaiApiKey
  return request('/settings/keys', { method: 'POST', body: JSON.stringify(body) })
}

export async function getWorkspaceStatus() {
  return request('/workspace/status')
}

export async function linkWorkspace(path) {
  return request('/workspace/link', {
    method: 'POST',
    body: JSON.stringify({ path }),
  })
}

export async function unlinkWorkspace() {
  return request('/workspace/unlink', { method: 'POST' })
}

export async function fetchWorkspaceSnapshot() {
  return request('/workspace/snapshot', { method: 'POST' })
}

export async function readWorkspaceFile(relPath) {
  const q = encodeURIComponent(relPath || '')
  return request(`/workspace/file?rel_path=${q}`)
}

export async function writeWorkspaceFile(relPath, content) {
  return request('/workspace/file', {
    method: 'PUT',
    body: JSON.stringify({ rel_path: relPath, content }),
  })
}

export async function listPendingApprovals(chatId = null) {
  const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : ''
  return request(`/agent/pending${q}`)
}

export async function resolveAgentApproval(approvalId, approve) {
  return request('/agent/approve', {
    method: 'POST',
    body: JSON.stringify({ approval_id: approvalId, approve: !!approve }),
  })
}

export async function searchChats(q) {
  const query = encodeURIComponent(q || '')
  return request(`/chat/search?q=${query}`)
}

export async function compactChat(chatId) {
  return request('/chat/compact', {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId }),
  })
}

export async function getChatHandoff(chatId) {
  return request(`/chat/handoff/${encodeURIComponent(chatId)}`)
}

export async function listBookmarks() {
  return request('/bookmarks')
}

export async function addBookmark(chatId, content, title = '') {
  return request('/bookmarks', {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId || '', content, title }),
  })
}

export async function deleteBookmark(id) {
  return request(`/bookmarks/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function getUsageStats() {
  return request('/observability/usage')
}

export async function pickWorkspaceFolderNative() {
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const path = await invoke('pick_workspace_folder')
    return path || null
  } catch {
    return null
  }
}
