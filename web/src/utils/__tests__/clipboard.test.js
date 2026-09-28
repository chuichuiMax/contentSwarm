import assert from 'node:assert/strict'
import { copyTextToClipboard } from '../clipboard.js'

const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
const originalDocument = Object.getOwnPropertyDescriptor(globalThis, 'document')

const setGlobal = (name, value) => {
  Object.defineProperty(globalThis, name, { configurable: true, value })
}

const restoreGlobal = (name, descriptor) => {
  if (descriptor) {
    Object.defineProperty(globalThis, name, descriptor)
  } else {
    delete globalThis[name]
  }
}

const run = async () => {
  try {
    {
      let copiedText = null
      setGlobal('navigator', {
        clipboard: {
          writeText: async (text) => {
            copiedText = text
          }
        }
      })
      setGlobal('document', undefined)

      await copyTextToClipboard('native clipboard')
      assert.equal(copiedText, 'native clipboard')
      console.log('T1 Clipboard API: PASS')
    }

    {
      const textArea = {
        style: {},
        value: '',
        setAttribute: () => {},
        focus: () => {},
        select: () => {},
        setSelectionRange: () => {},
        remove: () => {
          textArea.removed = true
        }
      }
      let command = null
      setGlobal('navigator', {
        clipboard: {
          writeText: async () => {
            throw new Error('NotAllowedError')
          }
        }
      })
      setGlobal('document', {
        activeElement: null,
        body: {
          appendChild: (node) => assert.equal(node, textArea)
        },
        createElement: (tag) => {
          assert.equal(tag, 'textarea')
          return textArea
        },
        execCommand: (value) => {
          command = value
          return true
        }
      })

      await copyTextToClipboard('HTTP fallback')
      assert.equal(textArea.value, 'HTTP fallback')
      assert.equal(command, 'copy')
      assert.equal(textArea.removed, true)
      console.log('T2 Rejected API falls back to copy command: PASS')
    }

    {
      setGlobal('navigator', {})
      setGlobal('document', undefined)
      await assert.rejects(() => copyTextToClipboard('unavailable'), /Clipboard API is unavailable/)
      console.log('T3 Unavailable clipboard reports failure: PASS')
    }
  } finally {
    restoreGlobal('navigator', originalNavigator)
    restoreGlobal('document', originalDocument)
  }
}

await run()
