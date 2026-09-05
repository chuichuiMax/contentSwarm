import assert from 'node:assert/strict'
import test from 'node:test'

import { drawTextLayer, pointInLayer } from '../coverEditorCanvas.js'

function canvasContext() {
  const calls = []
  return {
    calls,
    globalAlpha: 1,
    fillStyle: '#000000',
    strokeStyle: '#000000',
    lineWidth: 0,
    save() {},
    restore() {},
    translate() {},
    rotate() {},
    beginPath() {},
    roundRect() {},
    fill() {
      calls.push({ method: 'panel', fill: this.fillStyle, alpha: this.globalAlpha })
    },
    measureText(character) {
      return { width: character === ' ' ? 10 : 36 }
    },
    fillText(character, x, y) {
      calls.push({ method: 'fillText', character, x, y, fill: this.fillStyle })
    },
    strokeText(character, x, y) {
      calls.push({ method: 'strokeText', character, x, y, stroke: this.strokeStyle })
    }
  }
}

function textLayer(overrides = {}) {
  return {
    id: 'title',
    layer_type: 'text',
    text: '4大产品服务',
    x: 100,
    y: 100,
    width: 600,
    height: 120,
    rotation: 0,
    opacity: 1,
    visible: true,
    font_family: 'Noto Sans CJK SC',
    font_size: 64,
    font_weight: 700,
    font_style: 'normal',
    fill: '#E4E4E4',
    fill_runs: [
      { start: 0, end: 2, fill: '#F4DC28' },
      { start: 2, end: 6, fill: '#E4E4E4' }
    ],
    align: 'center',
    line_height: 1.2,
    letter_spacing: 0,
    stroke: false,
    shadow: false,
    background_fill: '#E7D6C9',
    background_opacity: 1,
    background_radius: 8,
    background_padding: 12,
    ...overrides
  }
}

test('renders saved multicolor runs and opaque text background without OCR processing', () => {
  const context = canvasContext()

  drawTextLayer(context, textLayer())

  const panel = context.calls.find((call) => call.method === 'panel')
  const glyphs = context.calls.filter((call) => call.method === 'fillText')
  assert.deepEqual(panel, { method: 'panel', fill: '#E7D6C9', alpha: 1 })
  assert.deepEqual(
    glyphs.map((call) => call.fill),
    ['#F4DC28', '#F4DC28', '#E4E4E4', '#E4E4E4', '#E4E4E4', '#E4E4E4']
  )
})

test('hit testing follows a rotated editable layer instead of the flattened image', () => {
  const layer = textLayer({ x: 100, y: 100, width: 200, height: 80, rotation: 30 })

  assert.equal(pointInLayer(layer, 200, 140), true)
  assert.equal(pointInLayer(layer, 20, 20), false)
})
