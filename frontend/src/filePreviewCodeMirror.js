/**
 * CodeMirror 6 language + theme helpers for the project file preview editor.
 * @see https://codemirror.net/
 * @see https://github.com/uiwjs/react-codemirror
 */

import { html } from '@codemirror/lang-html'
import { css } from '@codemirror/lang-css'
import { javascript } from '@codemirror/lang-javascript'
import { python } from '@codemirror/lang-python'
import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language'
import { oneDark } from '@codemirror/theme-one-dark'
import { EditorView } from '@codemirror/view'

const LANG_HTML = [html()]
const LANG_CSS = [css()]
const LANG_TSX = [javascript({ jsx: true, typescript: true })]
const LANG_TS = [javascript({ typescript: true })]
const LANG_JSX = [javascript({ jsx: true })]
const LANG_JS = [javascript()]
const LANG_PY = [python()]
const LANG_NONE = []

/** Lezer-based highlighting for paths we recognize; empty → plain text (no highlighting). */
export function languageExtensionsForPath(relPath) {
  const base = (String(relPath).split(/[/\\]/).pop() || '').toLowerCase()
  if (base.endsWith('.html') || base.endsWith('.htm')) return LANG_HTML
  if (base.endsWith('.css')) return LANG_CSS
  if (base.endsWith('.tsx')) return LANG_TSX
  if (base.endsWith('.ts')) return LANG_TS
  if (base.endsWith('.jsx')) return LANG_JSX
  if (base.endsWith('.js') || base.endsWith('.mjs') || base.endsWith('.cjs')) return LANG_JS
  if (base.endsWith('.py') || base.endsWith('.pyw')) return LANG_PY
  return LANG_NONE
}

/** Tokens roughly aligned with app light theme (theme.css --bg-deep / --text-primary). */
const adaLightChrome = EditorView.theme(
  {
    '&': {
      backgroundColor: '#f6f8fa',
      color: '#1f2328',
    },
    '.cm-scroller': {
      fontFamily: "ui-monospace, 'Consolas', 'Monaco', monospace",
      fontSize: '0.75rem',
      lineHeight: '1.5',
    },
    '.cm-content': {
      caretColor: '#0969da',
    },
    '.cm-cursor, .cm-dropCursor': {
      borderLeftColor: '#0969da',
    },
    '.cm-activeLine': {
      backgroundColor: 'rgba(9, 105, 218, 0.06)',
    },
    '&.cm-focused .cm-selectionBackground, & .cm-content ::selection': {
      backgroundColor: 'rgba(9, 105, 218, 0.25) !important',
    },
  },
  { dark: false },
)

const adaLightHighlight = syntaxHighlighting(defaultHighlightStyle, { fallback: true })

const adaLightTheme = [adaLightChrome, adaLightHighlight]

const adaDarkTypography = EditorView.theme(
  {
    '.cm-scroller': {
      fontFamily: "ui-monospace, 'Consolas', 'Monaco', monospace",
      fontSize: '0.75rem',
      lineHeight: '1.5',
    },
  },
  { dark: true },
)

const DARK_EXTENSIONS = [oneDark, adaDarkTypography]

/** Dark: one-dark + app typography; light: GitHub-like chrome + default highlight styles. */
export function themeExtensionsForScheme(colorScheme) {
  if (colorScheme === 'light') return adaLightTheme
  return DARK_EXTENSIONS
}
