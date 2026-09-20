/** Strip ```jarvis-file:...``` / ```ada-file:...``` blocks for live streaming display. */
const FILE_BLOCK = /^```\s*(?:jarvis|ada)-file:([^\n`]+)\s*\n(.*?)```\s*/gms

export function stripJarvisFileFencesForDisplay(text) {
  if (!text) return ''
  return text.replace(FILE_BLOCK, '').replace(/\n{3,}/g, '\n\n')
}
