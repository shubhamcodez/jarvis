# Jarvis Agent Workflow

End-to-end flow from user message to reply: routing, supervisor, desktop/coding/shell/**finance** agents, **memory** (planned), and **self-improving evals**.

---

## 1. Entry points

- **POST `/chat/send-message`** (non-streaming): single request/response; used when the client does not need streaming.
- **POST `/chat/send-message/stream`** (streaming): SSE stream; used by the frontend. Can stream chat tokens **or** run the agent and send one final event with the full reply.
- **POST `/chat/response`**: chat-only, no routing (direct LLM reply).

The **provider** (OpenAI or xAI) and **API key** come from `get_llm_provider()` and `get_llm_api_key()` (Settings updates `backend/jarvis-config.yaml`; keys stay in `.env`).

---

## 2. Streaming path (main UX)

The frontend calls **`sendMessageStream`** → **POST `/chat/send-message/stream`**.

1. **Attachments-only or empty message**  
   - If there is no message but there are attachments, the backend streams a **chat** reply (summarize/answer based on docs).  
   - If the message is empty and no attachments, it streams a reply to "Hello."  
   - No supervisor or agent; stream ends with `{ done: true, reply }`.

2. **Message present**  
   - **Supervisor** is called once: `supervisor_decision(api_key, provider, message)` (sync, in a thread).  
   - Returns: `run_agent` (bool), `agent` ("desktop" | "coding" | "shell" | "finance" | null), `goal`, `reasoning`, `next_steps`.

3. **If `run_agent` is false or no goal**  
   - Backend streams the **chat** reply (same LLM, no agent): `_stream_chat_reply(api_key, message, attachment_paths)` → SSE `delta` chunks, then `{ done: true, reply }`.

4. **If `run_agent` is true and goal is set**  
   - Backend builds **initial_state** (message, attachment_paths, chat_id, api_key, provider, **on_step**), starts a **drain_steps** task, and runs the **LangGraph** router with `graph.ainvoke(initial_state)`.  
   - Steps emitted by the agent are put into a queue; **drain_steps** reads the queue and calls **`_emit_agent_step`** for each payload (step, thought, action, description, result, done, optional screenshot).  
   - **`_emit_agent_step`** broadcasts that payload to all WebSocket clients on **`/ws/agent-steps`**.  
   - When the graph finishes, the stream yields one final SSE event: `{ done: true, reply }`.

So for the user: either they see **streaming chat text** or **agent steps (and screenshots) on the WebSocket**, then the **final reply** in the SSE stream.

---

## 3. Non-streaming path (POST `/chat/send-message`)

- Same **initial_state** and **LangGraph** `graph.ainvoke(initial_state)`.  
- **on_step** pushes to a queue; a **drain_steps** task runs in the background and calls **`_emit_agent_step`** so any connected WebSocket clients still see steps.  
- No SSE; the HTTP response is returned after the graph completes with `{ reply }`.

---

## 4. LangGraph router (single graph for both paths)

**State:** `RouterState` (message, attachment_paths, chat_id, api_key, provider, route, supervisor_decision, goal, reply, on_step).

**Graph shape:**

1. **Start**  
   - **Condition:** if there is no message but there are attachment_paths → go to **chat**; else go to **supervisor**.

2. **Supervisor node**  
   - Runs **supervisor_decision(api_key, provider, message)**.  
   - Writes **supervisor_decision** (including **`agents`**: ordered list of `{ agent, goal }`, up to 5) and **goal** (first step’s goal) into state.  
   - No `on_step` call here; the UI “Supervisor” step is emitted when the agent plan runs.

3. **After supervisor**  
   - If `run_agent` is false or **`agents`** is empty → **chat**.  
   - Else → **run_agent_plan** (runs **desktop / coding / shell / finance** steps **in order** inside one node).

4. **Chat node**  
   - Calls `client.chat(api_key, message, paths)` (current provider).  
   - Sets **reply** and **route: "chat"** in state → **END**.

5. **run_agent_plan node**  
   - **`_emit_supervisor_step(state)`** once, then for each entry in **`agents`**: optional web-search augment on the **first** step only; each specialist runs with **`on_step` offsets** so steps don’t collide; later steps receive truncated outputs from earlier agents as context.  
   - **Reply** is markdown sections (`### Desktop`, `### Coding`, …) concatenated.  
   - **route:** `run_multi_agent` if more than one section ran, else `run_desktop` / `run_coding` / `run_shell` / `run_finance` matching the single specialist.  
   - **tool_used:** last meaningful tool payload (e.g. sandbox, yfinance, web_search).  
   - **Shell** remains opt-in (`ADA_ENABLE_SHELL=1`). See **FINANCE.md** / **SHELL.md** for agent details.

So the **entire agent workflow** for a given message is: **Start → Supervisor → chat OR run_agent_plan → END**. The supervisor may schedule **one or several** specialists in sequence on a single user turn. There is **no** embedded Playwright browser agent; on-screen browser work uses **desktop** (real Chrome) or **chat** (links and instructions).

---

## 5. Supervisor (one LLM call)

- **Input:** api_key, provider, user message.  
- **Model:** Same as chat for that provider (e.g. GPT-4o or Grok).  
- **System prompt:** Describes five options and asks for either an **`agents` array** (ordered subtasks) or the compact single **`agent` + `goal`** form. **Finance** = yfinance + **prose**; **coding** = sandbox; **shell** = host terminal when enabled; **desktop** = visible browser/GUI.  
- **Heuristic:** Same overrides as before (coding / shell / finance signals); heuristics return a one-element **`agents` list**. Per-plan-entry sanitization reroutes desktop→coding/shell and finance→coding when the **sub-goal** text matches those patterns.  
- **Output:** `run_agent`, **`agents`** (`[{ "agent", "goal" }, …]`), plus **`agent` / `goal`** on the first step for backward compatibility, `reasoning`, `next_steps`.  
- Used only to **route**; the final reply is produced by **chat** or by **run_agent_plan** (which invokes the specialists).

---

## 6. Desktop agent (screenshot + vision + pyautogui)

- **Input:** goal, max_steps (e.g. 10), on_step, api_key, provider.  
- **Capabilities:** Drives the **real mouse cursor** and **keyboard** on the user’s machine: **click**, **double_click**, **right_click** (pixel coordinates from the screenshot), **scroll**, **type** (literal text), **press** (single keys: Enter, Tab, Esc, arrows, F-keys, …), **hotkey** (e.g. Ctrl+T, Alt+F4, Win+D).  
- **Flow:**  
  1. **Loop (step 1..max_steps):**  
     - **Capture screen** (mss) → image + width/height.  
     - **Vision LLM** (current provider): image + goal + step + last result + image dimensions → JSON **action** (one of the actions above, or **done**).  
     - If **done:** emit step and break.  
     - **Loop guard:** Repeated action 3 times → stop.  
     - **Execute:** pyautogui (click / doubleClick / rightClick / write / press / hotkey / scroll).  
     - **on_step(step, thought, action, description, result, screenshot)**.  
  2. Return a text summary of the trace.  

Again, **on_step** is the same callback; the backend sends these steps over the WebSocket.

---

## 6b. Coding agent (sandboxed Python)

- **Input:** goal (e.g. “calculate factorial of 10”, “plot AAPL daily returns”, “correlation of MSFT vs SPY”), `on_step`, api_key, provider.  
- **Flow:**  
  1. Emit **on_step(0, …)** with a short plan (interpret task → generate code → run in sandbox).  
  2. **LLM** outputs JSON `{"code": "..."}` using sandbox-allowed imports (stdlib + **numpy, pandas, matplotlib, yfinance**, etc.; see `backend/SANDBOX.md`). **`MPLBACKEND=Agg`** is set for headless matplotlib.  
  3. **run_sandboxed_python** in a child process (~**45s** timeout for slow network); on failure, one **retry** with the error returned to the LLM.  
  4. Further **on_step** calls for execute/done; final reply includes stdout (text in fenced blocks; **PNG/JPEG lines** or `ADA_IMAGE_PNG:…` (legacy `JARVIS_IMAGE_PNG`) become inline Markdown images in the UI). **tool_used** may record `python_sandbox`.  
- **No GUI** — does not use pyautogui or desktop automation for script tasks. For **market data + prose** without custom code, use the **finance** agent instead.

---

## 6c. Shell agent (host terminal, opt-in)

- **Input:** goal (e.g. “mkdir foo”, “list drives”, “run `git status`”), `on_step`, api_key, provider.  
- **Enable:** `ADA_ENABLE_SHELL=1` on the server (see `backend/SHELL.md`).  
- **Flow:** Multi-turn loop: LLM outputs JSON `{"done", "command", "thought}` → **run_shell_command** (PowerShell or bash under `ADA_SHELL_WORKDIR`) → stdout/stderr returned to the LLM until `done` or step limit.  
- **Not a security boundary** — same risk class as SSH on that machine; blocklist only catches a few catastrophic patterns.

---

## 6d. Finance agent (yfinance)

- **Input:** goal (e.g. “Compare AAPL and MSFT YTD”, “What’s Tesla’s P/E?”), `on_step`, api_key, provider.  
- **Dependency:** `yfinance` (see `backend/FINANCE.md`, `backend/MODELS.md`).  
- **Scope:** **Data + prose** (tables, ratios, comparisons). For **plots / regressions / scripted analysis**, the **coding** agent runs Python in the sandbox (see §6b, `SANDBOX.md`).  
- **Flow:**  
  1. **Planner LLM** → JSON: tickers, history period/interval, `include_financials`, restated question.  
  2. **`fetch_finance_bundle`** → trimmed `info`, optional price history summary + tail table, optional quarterly financials sample.  
  3. **Analyst LLM** → Markdown answer grounded in the JSON; disclaimer not financial advice.  
  4. **on_step:** plan (0), fetched (1), done (2); **tool_used** may record `yfinance`.

---

## 7. Step emission and WebSocket

- **on_step(step, thought, action, description, result, done, screenshot_base64=None)** is passed in state and called by:  
  - **Supervisor step (0):** emitted by **run_desktop** / **run_coding** / **run_shell** / **run_finance** via **`_emit_supervisor_step(state)`** (reasoning, next_steps).  
  - **Desktop:** each loop step.  
  - **Coding:** plan (0), generate/execute (1), done (2).  
  - **Shell:** plan (0), one **on_step** per command, then done.  
  - **Finance:** plan (0), fetch (1), done (2).  
- Each call pushes a payload to the **step_queue**. The **drain_steps** task (running alongside the graph) calls **`_emit_agent_step(...)`**, which **broadcasts** the payload to every WebSocket client connected to **`/ws/agent-steps`**.  
- Payload includes: step, thought, action, description, result, done, and optionally **screenshot** (base64). The frontend subscribes to this WebSocket and displays steps and screenshots in the chat UI.

---

## 8. Tracing and observability

- After the graph finishes (both stream and non-stream), the backend calls **trace_log(provider, route, message, reply, success, error, duration_sec, …)**.  
- **route** is the state’s **route** set by the node that ran (chat, run_desktop, run_coding, run_shell, run_finance), or **feedback_assess** when the user sends a short “this reply was bad” message (see §9).  
- Traces are appended to **jarvis-observability/traces/trace.jsonl** (or legacy **jarvis-observability** if that folder exists alone) and used later for optional eval generation and optimization (per-model success rates, tokens, errors).

---

## 9. Self-improving loop (evals → prompts & code)

**User-triggered review (primary “what went wrong?” path)** — When the user sends a short complaint in an existing chat (e.g. “I don’t like this response”, “doesn’t look right”), the frontend has already appended that line to the chat log. The backend **short-circuits** normal routing: it loads history **excluding** the complaint, takes the last user→assistant pair as the turn under review, optionally re-runs the **same user message** on the **other** provider (single turn, no history), and asks an LLM (prefers OpenAI) for diagnosis and **actionable fixes** (not only prompt tweaks—calculations, tools, routing, etc.). The reply streams like a normal assistant message; traces use **route `feedback_assess`**. For manual calls: POST `/observability/feedback-assess` with `{ "chat_id": "..." }`.

**Batch evals from traces (optional)** — The agent can also improve using **traces** and **batch eval results**:

1. **Trace** — Every chat/agent run is logged (see §8).  
2. **Generate evals** — POST `/observability/evals/generate`: an LLM turns recent traces into multi-turn eval cases; stored in `jarvis-observability/evals/eval_cases.jsonl` (same legacy-folder rule as traces). Background generation from traces is **off by default**; set `ADA_AUTO_EVAL_GEN=1` to re-enable on a cooldown.  
3. **Run evals** — POST `/observability/evals/run`: each case is run with **both** OpenAI and xAI; optional LLM judge scores replies; results in `eval_runs.jsonl`, pass@1 per model.  
4. **Optimization** — POST `/observability/optimization/run`: aggregates trace stats + eval pass rates, then calls an LLM to produce:
   - **prompt_modification_instructions** (target: supervisor | desktop | coding | shell | finance | chat; what to add/change and why),
   - **code_addition_suggestions** (file, suggestion, reason).  
5. **Apply** — You (or automation) edit prompts/code from those instructions; the next runs and evals reflect the improvement.

So: **complaint → feedback_assess (per chat)**; optionally **traces → evals → scores → optimization → apply → better agents**.

---

## 10. Memory architecture (planned)

Structured memory so the model can use past chats, docs, and code without filling the whole context.

**Four stores**

- **Raw chunk store** — chunk_id, content, source_type (chat, code, doc, note), source_id, created_at, version, metadata.  
- **Vector index** (Chroma) — chunk_id, embedding, metadata filters; fast semantic retrieval.  
- **Summary store** — summary_id, chunk_id/parent_id, short summary, facts/entities/decisions.  
- **Working state store** — current task, active files, recent decisions, unresolved questions, last retrieved chunk set.

**Per-turn flow (when memory is wired in)**

- **A. Parse request** — Intent, entities, active artifact, source type.  
- **B. Load task state** — Fetch working state for this session/conversation.  
- **C. Retrieve** — Query understanding → hybrid retrieval (vector + keyword + structural) → rerank → select summaries + raw chunks.  
- **D. Assemble prompt** — System + current request + task state + retrieved summaries + selected chunks (token budget: e.g. 10% task state, 20% retrieved, 35% raw, 20% response headroom).  
- **E. Generate** — Existing flow: supervisor → chat | desktop | coding | shell | finance → reply.  
- **F. Update memory** — Update working state; **write-back** only important turns (decisions, facts, artifacts) into raw store → chunk → summarize → embed → vector + summary stores.

Chunking: **chats** by 1–5 message windows (conversation_id, turn_range, decisions); **docs** by sections/paragraphs with overlap; **code** by file/symbol (no plain-text chunking). See README for full memory design.

---

## 11. Summary diagram

**Request/response (current)**

```
User sends message (frontend)
        ↓
POST /chat/send-message/stream  (or /chat/send-message)
        ↓
[Stream path] chat_id + complaint phrase? → feedback_assess (stream) → done
        ↓
[Stream path] Empty message + attachments? → Stream chat reply → done
[Stream path] Empty message? → Stream "Hello." reply → done
        ↓
supervisor_decision(api_key, provider, message)
        ↓
run_agent false or agents empty? → Stream chat reply (deltas + done) → end
        ↓
run_agent true, agents = [ … ]
        ↓
Build initial_state (message, paths, chat_id, api_key, provider, on_step)
Start drain_steps task (consumes step_queue → _emit_agent_step → WebSocket)
        ↓
LangGraph: __start__ → supervisor node → conditional:
        ├─ chat node           → reply + route "chat" → END
        └─ run_agent_plan node → _emit_supervisor_step + sequential desktop/coding/shell/finance → reply + route per run → END
        ↓
trace_log(provider, route, message, reply, ...)
        ↓
Stream: yield { done: true, reply }  |  Non-stream: return { reply }
```

**Self-improving loop (evals + optional batch)**

```
User complaint in chat  →  feedback_assess  →  diagnosis + alternate-model comparison
        (optional)
trace.jsonl (every run)
        ↓
POST /observability/evals/generate  →  eval_cases.jsonl (multi-turn cases from logs)
        ↓
POST /observability/evals/run  →  eval_runs.jsonl, pass@1 per model
        ↓
POST /observability/optimization/run  →  trace_stats + eval_pass + LLM
        ↓
prompt_modification_instructions + code_addition_suggestions
        ↓
Apply (human or automation)  →  better prompts/code  →  next runs improve
```

**Memory (planned) — per turn**

```
Parse request  →  Load task state  →  Retrieve (vector + keyword → rerank)
        →  Assemble prompt (task state + summaries + chunks, token budget)
        →  Generate (existing supervisor → chat | desktop | coding | shell | finance)
        →  Update working state  →  Write-back important turns (chunk → embed → store)
```

The **agent workflow** is: one supervisor call, then **chat** or **run_agent_plan** (one or more of **desktop** / **coding** / **shell** / **finance** in order); steps (and screenshots for desktop) stream over the WebSocket; the final answer is in **reply** and in the trace log. **Evals** use traces to produce prompt/code suggestions. **Memory** (when implemented) wraps each turn with retrieval and write-back for persistence and continuity.
