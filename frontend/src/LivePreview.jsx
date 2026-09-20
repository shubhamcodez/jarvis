import { useMemo, useState } from 'react'
import { mermaidFlowSvg } from './mermaidFlow'

function unwrapFence(raw) {
  const text = String(raw ?? '')
  return text.replace(/^\n/, '').replace(/\n$/, '')
}

function srcDocFor(lang, source) {
  const body = unwrapFence(source)
  const l = (lang || '').toLowerCase()
  if (l === 'mermaid') {
    const svg = mermaidFlowSvg(body)
    const inner = svg || `<pre style="padding:12px;font:13px/1.4 sans-serif">Could not render this diagram. Jarvis previews graph/flowchart fences locally.\n\n${body.replace(/</g, '&lt;')}</pre>`
    return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}</style></head><body>${inner}</body></html>`
  }
  if (l === 'svg' || body.trim().startsWith('<svg')) {
    return `<!doctype html><html><head><meta charset="utf-8"><style>html,body{margin:0;background:#fff}</style></head><body>${body}</body></html>`
  }
  if (/<html[\s>]/i.test(body) || /<!doctype/i.test(body)) {
    return body
  }
  return `<!doctype html><html><head><meta charset="utf-8"><style>body{margin:12px;font-family:sans-serif}</style></head><body>${body}</body></html>`
}

export function isPreviewLanguage(lang) {
  const l = (lang || '').toLowerCase()
  return l === 'html' || l === 'svg' || l === 'preview' || l === 'ada-preview' || l === 'mermaid'
}

export default function LivePreview({ language, children, className }) {
  const [tab, setTab] = useState('preview')
  const source = unwrapFence(children)
  const doc = useMemo(() => srcDocFor(language, source), [language, source])
  const kind = (language || 'html').toLowerCase()
  const label = kind === 'svg' ? 'SVG' : kind === 'mermaid' ? 'Mermaid' : 'HTML'

  return (
    <div className={`live-preview ${className || ''}`}>
      <div className="live-preview-bar">
        <span className="live-preview-label">{label} preview</span>
        <div className="live-preview-tabs">
          <button type="button" className={tab === 'preview' ? 'is-active' : ''} onClick={() => setTab('preview')}>
            Preview
          </button>
          <button type="button" className={tab === 'source' ? 'is-active' : ''} onClick={() => setTab('source')}>
            Source
          </button>
        </div>
      </div>
      {tab === 'source' ? (
        <pre className="live-preview-source">
          <code>{source}</code>
        </pre>
      ) : (
        <iframe
          className="live-preview-frame"
          title={`${label} live preview`}
          sandbox=""
          srcDoc={doc}
        />
      )}
    </div>
  )
}
