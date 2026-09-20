function detectDesktopShell() {
  if (typeof window === 'undefined') return false
  return Boolean(window.__TAURI_INTERNALS__ || window.__TAURI__)
}

let _apiToken = ''

export async function initApiAuth() {
  if (typeof window === 'undefined' || _apiToken) return
  if (!detectDesktopShell()) return
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    const t = await invoke('api_token')
    if (typeof t === 'string' && t.trim()) _apiToken = t.trim()
  } catch {
    /* browser / missing command */
  }
}

export function authHeaders() {
  return _apiToken ? { 'X-Ada-Token': _apiToken } : {}
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

async function request(path, options = {}) {
  const url = path.startsWith('http') ? path : `${getApiBase()}${path}`;
  const headers = { ...authHeaders(), ...(options.headers || {}) };
  if (options.body != null && !headers['Content-Type'] && !headers['content-type']) {
    headers['Content-Type'] = 'application/json';
  }
  const res = await fetch(url, {
    credentials: 'include',
    ...options,
    headers,
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
  customAgentId = null,
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
      custom_agent_id: customAgentId || null,
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
    onUsage,
    onRun = null,
    webSearchQuery = null,
    codingMode = false,
    codingProjectSnapshot = null,
    customAgentId = null,
    resumeTaskId = null,
    signal = null,
  },
) {
  const base = getApiBase()
  const res = await fetch(base + '/chat/send-message/stream', {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json', ...authHeaders() },
    signal,
    body: JSON.stringify({
      message: message || '',
      attachment_paths: attachmentPaths || null,
      chat_id: chatId || null,
      web_search_query: webSearchQuery || null,
      coding_mode: !!codingMode,
      coding_project_snapshot: codingProjectSnapshot || null,
      custom_agent_id: customAgentId || null,
      resume_task_id: resumeTaskId || null,
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
        let data
        try {
          data = JSON.parse(line.slice(6))
        } catch {
          continue
        }
        try {
          if (data.type === 'run' && data.run_id) {
            onRun?.(data.run_id)
            continue
          }
          if (data.type === 'status') {
            onStatus?.(data)
            continue
          }
          if (data.type === 'agent_step') {
            onAgentStep?.(data)
            continue
          }
          if (data.type === 'usage') {
            onUsage?.(data)
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
              task_id: data.task_id ?? null,
            }
          }
        } catch (_) {}
      }
    }
  }
  if (full) onDone?.(full, null)
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
  customAgentId = null,
  signal = null,
) {
  const form = new FormData();
  form.append('message', message || '');
  form.append('chat_id', chatId ?? '');
  if (webSearchQuery) form.append('web_search_query', webSearchQuery);
  if (codingMode) form.append('coding_mode', 'true');
  if (codingProjectSnapshot) form.append('coding_project_snapshot', codingProjectSnapshot);
  if (customAgentId) form.append('custom_agent_id', customAgentId);
  for (const f of files) {
    form.append('files', f);
  }
  const base = getApiBase();
  const url = base + '/chat/send-message-with-files';
  const res = await fetch(url, {
    method: 'POST',
    body: form,
    credentials: 'include',
    signal,
    headers: { ...authHeaders() },
  });
  if (!res.ok) throw new Error(await res.text() || `HTTP ${res.status}`);
  const data = await res.json();
  return data.reply;
}

export async function appendChatLog(role, content, chatId = null) {
  await request('/chat/append', {
    method: 'POST',
    body: JSON.stringify({ role, content, chat_id: chatId || undefined }),
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
  return request(`/chat/read/${encodeURIComponent(chatId)}`);
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

/** Current LLM provider: "openai" (GPT), "xai" (Grok), or "local". */
export async function getModelSetting() {
  const data = await request('/settings/model');
  return {
    provider: data?.provider || 'openai',
    local_model_id: data?.local_model_id || '',
  };
}

export async function setModelSetting(provider, localModelId = null) {
  let p = 'openai'
  let mid = localModelId
  if (typeof provider === 'string' && provider.startsWith('local:')) {
    p = 'local'
    mid = provider.slice(6)
  } else if (provider === 'xai') {
    p = 'xai'
  } else if (provider === 'local') {
    p = 'local'
  }
  await request('/settings/model', {
    method: 'POST',
    body: JSON.stringify({ provider: p, local_model_id: mid || null }),
  });
  return p;
}

export async function getLocalModels() {
  return request('/settings/local-models')
}

export async function getLocalModelJob() {
  return request('/settings/local-models/status')
}

export async function downloadLocalModel(modelId) {
  return request('/settings/local-models/download', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  })
}

export async function loadLocalModel(modelId) {
  return request('/settings/local-models/load', {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  })
}

/** WebSocket URL for agent steps (use wsOrigin for WS) */
export function agentStepsWsUrl() {
  const base = getApiBase() === '/api' ? (import.meta.env.VITE_API_URL || '') : getApiBase();
  let url
  if (base.startsWith('http://')) {
    url = base.replace('http://', 'ws://') + '/ws/agent-steps';
  } else if (base.startsWith('https://')) {
    url = base.replace('https://', 'wss://') + '/ws/agent-steps';
  } else {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.host;
    url = `${proto}//${host}/ws/agent-steps`;
  }
  if (_apiToken) {
    url += (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(_apiToken)
  }
  return url
}

export async function getGoogleAuthStatus() {
  return request('/auth/google/status');
}

/** Gmail profile for current session; does not throw on HTTP errors (returns { ok: false, error }). */
export async function getGmailProfile() {
  const base = getApiBase();
  const res = await fetch(`${base}/integrations/gmail/profile`, {
    credentials: 'include',
    headers: { ...authHeaders() },
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    return { ok: false, error: data.error || `HTTP ${res.status}`, detail: data.detail };
  }
  return data;
}

export function getGoogleAuthLoginUrl(nextPath = '/settings') {
  const base = getApiBase()
  const path = nextPath && nextPath.startsWith('/') ? nextPath : '/settings'
  let url = `${base}/auth/google/login?next=${encodeURIComponent(path)}`
  if (_apiToken) url += `&token=${encodeURIComponent(_apiToken)}`
  return url
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

export async function getChatRecap(chatId) {
  return request(`/chat/recap/${encodeURIComponent(chatId)}`)
}

export async function forkChat(chatId, messageIndex, label = '') {
  return request('/chat/fork', {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId, message_index: messageIndex, label }),
  })
}

export async function listChatBranches(chatId) {
  return request(`/chat/branches/${encodeURIComponent(chatId)}`)
}

export async function getChatMeta(chatId) {
  return request(`/chat/meta/${encodeURIComponent(chatId)}`)
}

export async function mergeChat(sourceId, targetId = '') {
  return request('/chat/merge', {
    method: 'POST',
    body: JSON.stringify({ source_id: sourceId, target_id: targetId || '' }),
  })
}

export async function reactToReply(chatId, vote, excerpt = '') {
  return request('/chat/reaction', {
    method: 'POST',
    body: JSON.stringify({ chat_id: chatId || '', vote, excerpt }),
  })
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

export async function runSlash(command, args = '', chatId = '') {
  return request('/chat/slash', {
    method: 'POST',
    body: JSON.stringify({ command, args: args || '', chat_id: chatId || '' }),
  })
}

export async function rewindChat(chatId, keepCount = null) {
  return request('/chat/rewind', {
    method: 'POST',
    body: JSON.stringify({
      chat_id: chatId,
      keep_count: keepCount == null ? null : keepCount,
    }),
  })
}

export async function getSlashCatalog() {
  return request('/workspace/slash-catalog')
}

export async function getObservabilitySpans(limit = 200, traceId) {
  const q = new URLSearchParams({ limit: String(limit) })
  if (traceId) q.set('trace_id', traceId)
  return request(`/observability/spans?${q}`)
}

export async function getObservabilityMetrics() {
  return request('/observability/metrics')
}

export async function getObservabilityLogs(limit = 200) {
  return request(`/observability/logs?limit=${limit}`)
}

export async function getMemoryStatus() {
  return request('/memory/status')
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

export async function listCustomAgents(includeHidden = true) {
  const q = includeHidden ? '?include_hidden=true' : '?include_hidden=false'
  return request(`/custom-agents${q}`)
}

export async function getCustomAgent(agentId) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}`)
}

export async function createCustomAgent(nameOrFields = 'New Agent') {
  const body =
    nameOrFields && typeof nameOrFields === 'object'
      ? {
          name: nameOrFields.name || 'New Agent',
          brief: nameOrFields.brief || '',
          title: nameOrFields.title || null,
          description: nameOrFields.description || null,
          emoji: nameOrFields.emoji || null,
        }
      : { name: nameOrFields || 'New Agent' }
  return request('/custom-agents', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export async function patchCustomAgent(agentId, patch) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch || {}),
  })
}

export async function duplicateCustomAgent(agentId) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}/duplicate`, { method: 'POST' })
}

export async function deleteCustomAgent(agentId) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}`, { method: 'DELETE' })
}

export async function saveCustomAgentMemory(agentId, content) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}/memory`, {
    method: 'PUT',
    body: JSON.stringify({ content: content || '' }),
  })
}

export async function saveCustomAgentSkill(agentId, { name, description, body, skillId }) {
  return request(`/custom-agents/${encodeURIComponent(agentId)}/skills`, {
    method: 'PUT',
    body: JSON.stringify({
      name,
      description,
      body,
      skill_id: skillId || null,
    }),
  })
}

export async function deleteCustomAgentSkill(agentId, skillId) {
  return request(
    `/custom-agents/${encodeURIComponent(agentId)}/skills/${encodeURIComponent(skillId)}`,
    { method: 'DELETE' },
  )
}

export async function uploadCustomAgentKnowledge(agentId, files) {
  const form = new FormData()
  for (const f of files || []) form.append('files', f)
  const base = getApiBase()
  const res = await fetch(`${base}/custom-agents/${encodeURIComponent(agentId)}/knowledge`, {
    method: 'POST',
    body: form,
    credentials: 'include',
    headers: { ...authHeaders() },
  })
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
  return res.json()
}

export async function deleteCustomAgentKnowledge(agentId, filename) {
  return request(
    `/custom-agents/${encodeURIComponent(agentId)}/knowledge/${encodeURIComponent(filename)}`,
    { method: 'DELETE' },
  )
}

export async function saveCustomAgentRoutine(agentId, routine, routineId = null) {
  const path = routineId
    ? `/custom-agents/${encodeURIComponent(agentId)}/routines/${encodeURIComponent(routineId)}`
    : `/custom-agents/${encodeURIComponent(agentId)}/routines`
  return request(path, {
    method: routineId ? 'PUT' : 'POST',
    body: JSON.stringify(routine),
  })
}

export async function deleteCustomAgentRoutine(agentId, routineId) {
  return request(
    `/custom-agents/${encodeURIComponent(agentId)}/routines/${encodeURIComponent(routineId)}`,
    { method: 'DELETE' },
  )
}

export async function testCustomAgentRoutine(agentId, routineId) {
  return request(
    `/custom-agents/${encodeURIComponent(agentId)}/routines/${encodeURIComponent(routineId)}/test`,
    { method: 'POST' },
  )
}

export async function getCustomAgentToolCatalog() {
  return request('/custom-agents/tools')
}

export async function setRunMode(runMode) {
  return request('/settings/run-mode', {
    method: 'POST',
    body: JSON.stringify({ run_mode: runMode }),
  })
}

export async function setSpendLimits(maxTokens, warnTokens) {
  return request('/settings/spend', {
    method: 'POST',
    body: JSON.stringify({
      max_tokens_per_run: maxTokens,
      warn_tokens: warnTokens,
    }),
  })
}

export async function setQuietHours(payload) {
  return request('/settings/quiet-hours', {
    method: 'POST',
    body: JSON.stringify(payload || {}),
  })
}

export async function listTasks(chatId = null, openOnly = false) {
  const q = new URLSearchParams()
  if (chatId) q.set('chat_id', chatId)
  if (openOnly) q.set('open_only', 'true')
  const suffix = q.toString() ? `?${q}` : ''
  return request(`/tasks${suffix}`)
}

export async function cancelTask(taskId) {
  return request(`/tasks/${encodeURIComponent(taskId)}/cancel`, { method: 'POST' })
}

export async function listActiveRuns(chatId = null) {
  const q = chatId ? `?chat_id=${encodeURIComponent(chatId)}` : ''
  return request(`/agent/runs${q}`)
}

export async function stopActiveRun(runId) {
  return request(`/agent/runs/${encodeURIComponent(runId)}/stop`, { method: 'POST' })
}

export async function steerActiveRun(runId, note) {
  return request(`/agent/runs/${encodeURIComponent(runId)}/steer`, {
    method: 'POST',
    body: JSON.stringify({ note }),
  })
}

export async function listCheckpoints(runId = null) {
  const q = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
  return request(`/agent/checkpoints${q}`)
}

export async function restoreCheckpoint(checkpointId) {
  return request('/agent/checkpoints/restore', {
    method: 'POST',
    body: JSON.stringify({ checkpoint_id: checkpointId }),
  })
}

export async function listFacts() {
  return request('/memory/facts')
}

export async function addFact(text, key = '') {
  return request('/memory/facts', {
    method: 'POST',
    body: JSON.stringify({ text, key: key || null }),
  })
}

export async function deleteFact(factId) {
  return request(`/memory/facts/${encodeURIComponent(factId)}`, { method: 'DELETE' })
}

export async function getIdentity() {
  return request('/memory/identity')
}

export async function saveIdentity(payload) {
  return request('/memory/identity', {
    method: 'PUT',
    body: JSON.stringify(payload || {}),
  })
}
