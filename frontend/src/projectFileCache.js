/**
 * Full-text + image preview cache (IndexedDB). Explorer previews read from disk when a directory
 * handle exists; this cache backs text and image previews after reload or folder upload flows.
 */

import {
  readFileAsUtf8Limited,
  wantFileBody,
  PREVIEW_MAX_BYTES,
  fileToImageDataUrl,
  isProjectImagePath,
  IMAGE_PREVIEW_MAX_BYTES,
} from './projectSnapshot.js'

const DB_NAME = 'socrates-preview-cache'
const STORE = 'files'
const DB_VERSION = 1

function normRel(rel) {
  return String(rel).replace(/\\/g, '/').trim()
}

function makeKey(rootLabel, relPath) {
  return `${String(rootLabel).trim()}\x1f${normRel(relPath)}`
}

function makeImageKey(rootLabel, relPath) {
  return `${String(rootLabel).trim()}\x1fimg\x1f${normRel(relPath)}`
}

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onerror = () => reject(req.error)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE)
    }
    req.onsuccess = () => resolve(req.result)
  })
}

/** @returns {Promise<boolean>} */
export async function putPreviewFileText(rootLabel, relPath, text) {
  const label = String(rootLabel || '').trim()
  const rel = normRel(relPath)
  if (!label || !rel) return false
  let db
  try {
    db = await openDb()
  } catch {
    return false
  }
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.oncomplete = () => {
      db.close()
      resolve(true)
    }
    tx.onerror = () => {
      try {
        db.close()
      } catch {
        /* ignore */
      }
      reject(tx.error)
    }
    tx.objectStore(STORE).put(typeof text === 'string' ? text : String(text ?? ''), makeKey(label, rel))
  }).catch(() => false)
}

/** @returns {Promise<string|null>} */
export async function getPreviewImageDataUrl(rootLabel, relPath) {
  const label = String(rootLabel || '').trim()
  const rel = normRel(relPath)
  if (!label || !rel) return null
  let db
  try {
    db = await openDb()
  } catch {
    return null
  }
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const getReq = tx.objectStore(STORE).get(makeImageKey(label, rel))
    getReq.onsuccess = () => {
      db.close()
      const v = getReq.result
      resolve(typeof v === 'string' && v.startsWith('data:') ? v : null)
    }
    getReq.onerror = () => {
      db.close()
      reject(getReq.error)
    }
  }).catch(() => null)
}

/** @returns {Promise<string|null>} */
export async function getPreviewFileText(rootLabel, relPath) {
  const label = String(rootLabel || '').trim()
  const rel = normRel(relPath)
  if (!label || !rel) return null
  let db
  try {
    db = await openDb()
  } catch {
    return null
  }
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readonly')
    const getReq = tx.objectStore(STORE).get(makeKey(label, rel))
    getReq.onsuccess = () => {
      db.close()
      const v = getReq.result
      resolve(typeof v === 'string' ? v : null)
    }
    getReq.onerror = () => {
      db.close()
      reject(getReq.error)
    }
  }).catch(() => null)
}

function putMany(db, rootLabel, relToText) {
  const label = String(rootLabel).trim()
  if (!relToText.length) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    const store = tx.objectStore(STORE)
    for (const [rel, text] of relToText) {
      if (text) store.put(text, makeKey(label, rel))
    }
  })
}

function putImageMany(db, rootLabel, relToDataUrl) {
  const label = String(rootLabel).trim()
  if (!relToDataUrl.length) return Promise.resolve()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error)
    const store = tx.objectStore(STORE)
    for (const [rel, dataUrl] of relToDataUrl) {
      if (dataUrl) store.put(dataUrl, makeImageKey(label, rel))
    }
  })
}

export async function clearPreviewCacheForRoot(rootLabel) {
  const label = String(rootLabel || '').trim()
  if (!label) return
  let db
  try {
    db = await openDb()
  } catch {
    return
  }
  const prefix = `${label}\x1f`
  try {
    await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, 'readwrite')
      const store = tx.objectStore(STORE)
      const req = store.openCursor()
      req.onerror = () => reject(req.error)
      req.onsuccess = (e) => {
        const cursor = e.target.result
        if (cursor) {
          const key = cursor.key
          if (typeof key === 'string' && key.startsWith(prefix)) cursor.delete()
          cursor.continue()
        }
      }
      tx.oncomplete = () => resolve()
      tx.onerror = () => reject(tx.error)
    })
  } catch {
    /* ignore */
  } finally {
    try {
      db.close()
    } catch {
      /* ignore */
    }
  }
}

/**
 * Store full file text for every text-like indexed file (same rules as snapshot bodies).
 * @param {string} rootLabel
 * @param {{ rel: string, file: File }[]} records
 */
export async function cachePreviewFilesFromRecords(rootLabel, records) {
  const label = String(rootLabel || '').trim()
  if (!label || !records?.length) return
  await clearPreviewCacheForRoot(label)
  let db
  try {
    db = await openDb()
  } catch {
    return
  }
  const tasks = records.filter((r) => r?.rel && wantFileBody(r.rel))
  const imageTasks = records.filter((r) => r?.rel && isProjectImagePath(r.rel))
  const chunk = 24
  try {
    for (let i = 0; i < tasks.length; i += chunk) {
      const slice = tasks.slice(i, i + chunk)
      const reads = await Promise.all(
        slice.map(async ({ rel, file }) => {
          try {
            const text = await readFileAsUtf8Limited(file, PREVIEW_MAX_BYTES)
            const t = typeof text === 'string' ? text : ''
            return t.length ? [rel, t] : null
          } catch {
            return null
          }
        }),
      )
      const pairs = reads.filter(Boolean)
      if (pairs.length) await putMany(db, label, pairs)
    }
    for (let i = 0; i < imageTasks.length; i += chunk) {
      const slice = imageTasks.slice(i, i + chunk)
      const reads = await Promise.all(
        slice.map(async ({ rel, file }) => {
          try {
            const dataUrl = await fileToImageDataUrl(file, rel, IMAGE_PREVIEW_MAX_BYTES)
            return dataUrl ? [rel, dataUrl] : null
          } catch {
            return null
          }
        }),
      )
      const pairs = reads.filter(Boolean)
      if (pairs.length) await putImageMany(db, label, pairs)
    }
  } finally {
    try {
      db.close()
    } catch {
      /* ignore */
    }
  }
}
