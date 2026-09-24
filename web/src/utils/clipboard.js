export const copyTextToClipboard = async (value) => {
  const text = String(value ?? '')

  if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return
    } catch {
      // HTTP/IP deployments can expose the API while rejecting clipboard access.
    }
  }

  if (
    typeof document === 'undefined' ||
    !document.body ||
    typeof document.execCommand !== 'function'
  ) {
    throw new Error('Clipboard API is unavailable')
  }

  const textArea = document.createElement('textarea')
  const activeElement = document.activeElement
  textArea.value = text
  textArea.setAttribute('readonly', '')
  textArea.style.position = 'fixed'
  textArea.style.opacity = '0'
  textArea.style.pointerEvents = 'none'
  document.body.appendChild(textArea)

  try {
    textArea.focus()
    textArea.select()
    textArea.setSelectionRange?.(0, text.length)
    if (!document.execCommand('copy')) {
      throw new Error('Clipboard copy command failed')
    }
  } finally {
    textArea.remove()
    activeElement?.focus?.()
  }
}
