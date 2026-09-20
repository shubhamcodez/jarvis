/** Browser / Tauri notifications when Jarvis finishes or needs HITL (Claude Code notify hook). */

export async function ensureNotifyPermission() {
  if (typeof window === 'undefined' || typeof Notification === 'undefined') return 'denied'
  if (Notification.permission === 'granted') return 'granted'
  if (Notification.permission === 'denied') return 'denied'
  try {
    return await Notification.requestPermission()
  } catch {
    return 'denied'
  }
}

export function notifyJarvis(title, body) {
  if (typeof document !== 'undefined' && document.visibilityState === 'visible') return
  if (typeof Notification === 'undefined' || Notification.permission !== 'granted') return
  try {
    new Notification(title || 'Jarvis', { body: String(body || '').slice(0, 180) })
  } catch {
    /* ignore */
  }
}
