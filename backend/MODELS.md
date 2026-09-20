# LLM models (OpenAI)

- **Default (reasoning):** `gpt-5.4` for chat, stream, classify, vision (desktop), and all agent LLM calls when `backend/jarvis-config.yaml` has `llm_provider: openai`.
- **Override:** set in the project root `.env` or environment:
  - `OPENAI_CHAT_MODEL` — e.g. `gpt-5-mini`, `o4-mini`, `gpt-4o`
  - `OPENAI_VISION_MODEL` — vision / screenshot steps (defaults to same as chat if unset)

GPT-5 / o-series models use `max_completion_tokens` on Chat Completions; the code maps limits automatically. `o1` / `o3` / `o4` models skip custom `temperature` where required.

If `gpt-5.4` is unavailable for your org, set e.g. `OPENAI_CHAT_MODEL=o4-mini` and `OPENAI_VISION_MODEL=o4-mini`.

xAI uses `XAI_CHAT_MODEL` / `XAI_VISION_MODEL` in `agents/models/xai_client.py`.

Host **shell** agent: enable with `ADA_ENABLE_SHELL=1`; see `backend/SHELL.md`.

**Finance** agent uses **yfinance** (install: `pip install yfinance` / `poetry add yfinance`). The **coding** agent sandbox also allows **matplotlib**, **numpy**, **pandas**, and **yfinance** for plots and quantitative analysis (`poetry add matplotlib numpy pandas` or `pip install …`). Data is informational, not financial advice.
