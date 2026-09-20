# Python sandbox (coding agent)

The coding agent runs model-generated Python in a **child process** with a **timeout**, restricted **builtins**, and an **import allowlist** (`tools/sandbox_worker.py`).

## Allowed packages (high level)

- **Stdlib helpers:** `math`, `json`, `itertools`, `collections`, `statistics`, `datetime`, `random`, `re`, `io`, `base64`, `csv`, `hashlib`, `typing`, `warnings`, and related small stdlib modules used by HTTP clients.
- **Data / charts / market data:** `numpy`, `pandas`, `matplotlib` (set **`MPLBACKEND=Agg`** in the runner for headless use), `yfinance`, plus their typical dependencies (`requests`, `urllib3`, `lxml`, `curl_cffi`, etc.).

`tools/python_sandbox.py` sets `MPLBACKEND=Agg` so plots do not require a display.

## Charts in the chat UI

Print **one line per figure** (no line breaks inside the base64):

```text
ADA_IMAGE_PNG:<base64>
```

Example:

```python
import base64, io
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
buf = io.BytesIO()
plt.plot([1, 2, 3])
plt.savefig(buf, format="png")
print("ADA_IMAGE_PNG:" + base64.b64encode(buf.getvalue()).decode())
```

The coding agent turns that into a Markdown image so the frontend renders it. A **single stdout line** that is raw PNG base64 (`iVBOR…`) is also detected.

## HTML / SVG live previews

Print one line (raw markup, or base64 of the markup):

```text
ADA_PREVIEW_HTML:<div>hello</div>
ADA_PREVIEW_SVG:<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="teal"/></svg>
ADA_PREVIEW_MERMAID:graph TD; A[Start] --> B[Done]
```

The chat UI renders those fences in a sandboxed iframe (no scripts). You can also put ` ```html ` / ` ```svg ` / ` ```mermaid ` fences in a normal reply.

**Logs & tool cards:** `stdout` in `tool_used`, WebSocket step payloads, retry prompts, and `POST /tools/python-sandbox` responses use **redacted** stdout (chart lines replaced with `[chart image hidden]`) so JSON isn’t huge. The assistant **reply** still contains rendered images.

## Security

This is **not** a cryptographic sandbox. Allowing **HTTP** (yfinance / requests) means outbound network access from the worker. Do not expose to untrusted users without extra isolation (VM/container).

## Timeouts

The coding agent uses a **45s** sandbox timeout by default (network fetches can be slow).
