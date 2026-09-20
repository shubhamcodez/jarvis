/** Strip ```ada-file:...``` blocks for live streaming display (complete blocks only). */
const ADA_FILE_BLOCK = /^```\s*ada-file:([^\n`]+)\s*\n(.*?)```\s*/gms

export function stripJarvisFileFencesForDisplay(text) {
  if (!text) return ''
  return text.replace(ADA_FILE_BLOCK, '').replace(/\n{3,}/g, '\n\n')
}
