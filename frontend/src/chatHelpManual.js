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
| \`/stop\` | Cancel the current run. |

## Sending messages

- **Enter** — Send. While Ada is working, Enter **queues** the follow-up (does not kill the run).
- **Tab** while a run is active — Queue the composer (Codex-style deferred follow-up).
- **Hold / Resume** on the queue — Keep follow-ups visible without auto-sending (review first).
- **Stop** — Abort the current stream. The queue stays unless you remove items.
- **Shift+Enter** — New line in the composer.
- **Pin** on a reply — Save it in Activity.
- Search chats from the Chats menu.

Project rules: if a folder is linked, Ada reads \`ADA.md\`, \`AGENTS.md\`, \`CLAUDE.md\`, or \`.ada/rules.md\`.
- Use the **+** button to attach files or toggle **Web search** (when on, your message is also used as a search query unless you use the web-search flow from the menu).

## Chats

- Open and switch conversations from the chats list; each chat keeps its own history on disk (via the backend).

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
