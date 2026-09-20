/** Markdown shown when the user sends `/help` in chat (local only; not sent to the model). */
export const CHAT_HELP_MANUAL_MARKDOWN = `
# Chat manual

## Quick commands

| Command | Action |
| -------- | ------ |
| \`/introduce\` or \`/introduce/\` | Step‑through questionnaire that fills **user profile** (saved as JSON on the server). Does not call the assistant. |
| \`/help\` | Show this manual (stored in your chat log; does not call the assistant). |
| \`/compact\` | Visible summary of earlier turns (keeps the recent thread). |
| \`/handoff\` | Export a paste-ready context dump (also copied to the clipboard). |
| \`/recap\` | Structured recovery: current task, last result, files, next step. |
| \`/btw …\` or \`/side …\` | Side question that does **not** start an agent plan. |
| \`/search-memory …\` | Search other chats for a past decision or fix. |
| **Fork** on a message | New chat from that point (try another approach). **Merge back** returns findings to the parent. |
| \`/diff\` | Git status + unstaged/staged diff of the linked workspace. |
| \`/review\` | Read-only pass over the current diff (cheap risk flags, no extra model call). |
| \`/rewind\` [N] | Drop the last reply, or keep the first N messages. Also on the last assistant message. |
| \`/undo\` | Restore the last workbench edit, else the latest server checkpoint. |
| \`/usage\` / \`/cost\` / \`/stats\` | Token totals for recent traces. |
| \`/init\` | Write a starter \`AGENTS.md\` if missing (\`/init force\` replaces). |
| \`/doctor\` | Setup checkup (keys present, workspace, git, skills) — never prints secret values. |
| \`/commands\` | List workspace skills and file-based commands. |
| \`/skill name …\` or \`/name\` | Run a \`SKILL.md\` or \`.agents/commands/*.md\` playbook (\`$ARGUMENTS\` expanded). |
| \`/export\` | Download the full transcript as Markdown (also copied when allowed). |
| \`/rename [name]\` | Rename this chat. Omit the name to auto-title from the first real message. |
| \`/copy [N]\` | Copy the last assistant reply (or the Nth-latest) to the clipboard. |
| \`/context\` | Estimated tokens in this chat plus recent usage. |
| \`/plan [task]\` | Switch to Plan mode (no writes). With a task, start that planning turn. |
| \`/notify\` | Ask the browser for desktop alerts when a run finishes or needs approval (background tab). |
| \`/loop [5m] [prompt]\` | Repeat a short check-in while the backend is up (\`/loop stop\`). Mentions of tests run workspace tests read-only. |
| \`/tasks\` or \`/bashes\` | Durable tasks plus in-flight runs. |
| \`/goal [condition]\` | Pin a done-condition; the critic runs once after each turn (\`/goal clear\`). |
| Type \`/\` | Autocomplete built-in, workspace, and skill commands. |
| Apply all / Discard | Bar above chat when Ada proposed file edits — write or drop the whole batch. |
| \`/plan show\` | Last SWE \`update_plan\` (also kept on the chat and shown as the todo strip). |
| \`/model [openai\\|xai\\|local]\` | Show or switch the LLM provider. |
| \`/cd [path]\` | Show or relink the project workspace. |
| \`/effort low\\|medium\\|high\` | Spend cap per run (20k / 80k / 200k tokens). |
| Mermaid fences | \`\`\`mermaid graph/flowchart blocks render locally; other diagram types show a fallback. |
| HTML / SVG fences | Assistant \`html\` / \`svg\` blocks (or \`ADA_PREVIEW_*\` from the sandbox) render as a sandboxed live preview. |
| \`/stop\` | Cancel the current run. |
| \`remember …\` | Store an exact fact (decays if unused). |

## Control plane

Above the composer: **Plan** (no writes), **Draft** (approve every write), **Agent** (uses Settings → Autonomy). A live token meter stops the run at the spend cap. Open tasks persist across crashes — Resume continues the remaining plan. **Steer** injects an instruction into the next specialist; **Stop** cancels the in-flight run. File writes keep checkpoints (Settings → Checkpoints). Scheduled routines honor quiet hours and can run isolated or into the current chat.

## Sending messages

- **Enter** — Send. While Ada is working, Enter **queues** the follow-up (does not kill the run).
- **Tab** while a run is active — Queue the composer (Codex-style deferred follow-up).
- **Hold / Resume** on the queue — Keep follow-ups visible without auto-sending (review first).
- **Stop** — Abort the current stream. The queue stays unless you remove items.
- **Shift+Enter** — New line in the composer.
- **Pin** on a reply — Save it in Activity.
- Search chats from the Chats menu.

Project rules: if a folder is linked, Ada reads \`ADA.md\`, \`AGENTS.md\`, \`CLAUDE.md\`, or \`.ada/rules.md\`.
Workspace skills: \`SKILL.md\` files under \`.agents/skills/\`, \`.ada/skills/\`, \`.cursor/skills/\`, or \`.claude/skills/\` (names always listed; full playbook loads when relevant).
Thumbs on a reply store a lightweight signal (👎 also writes a short memory note). Say \`implement #64\` to fetch a GitHub issue via \`gh\`.
- Use the **+** button to attach files or toggle **Web search** (when on, your message is also used as a search query unless you use the web-search flow from the menu).

## Models

Settings → **Model** lists OpenAI, xAI, and local open-source weights. Ada detects GPU/NPU memory and suggests a Hugging Face GGUF (or a small transformers model). Download suggested, then pick it in the same menu.

## Chats

- Open and switch conversations from the chats list; each chat keeps its own history on disk (via the backend).

## Custom agents

The **Agents** menu in the navbar lists Ada plus your custom agents. Hover it to switch agents. The **+** button opens a modal — describe the job, and Ada creates the agent (memory, starter skill, tools).

- **Memory** — \`MEMORY.md\` (say \`remember …\` in chat to append a note)
- **Skills** — \`SKILL.md\` playbooks (description + steps; matching skills load on a turn)
- **Tools** — allowlist from Ada’s tool registry
- **Knowledge** — uploadable files (small files in context; larger ones searched)
- **Schedule** — routines that run at a time of day while the backend is up (drafts into the agent’s chat; test run from Configure)

Use **Configure** on an agent to edit those. Hide/pin/duplicate from Profile. Agent chats are kept out of the general Chats menu.

## Coding mode

When coding mode is on and a project folder is linked:

- **Explorer** — Browse project files; open a file to preview it in the **workbench** (center).
- **@\`path\` mentions** — Type \`@\` to suggest paths; use arrow keys to highlight, **Ctrl+click** to multi-select, **Enter** to insert. Mentioned files are included in context for Ada.
- **Proposed edits** — When Ada returns file changes, the workbench shows a diff; review, apply, or dismiss. Summary cards above the chat list stats per file; **click a card or a matching file heading** in the reply to jump to that file in the workbench.
- **Terminal** — Use the panel below the chat when available for shell commands (depends on your setup).

## Tips

- Link or re-open the project folder if the file tree is empty.
- Long assistant replies can be **copied** from the copy control on bot messages.

---

*This help text ships with the app. For server setup, agents, and OAuth, see the project README.*
`.trim()
