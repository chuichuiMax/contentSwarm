import assert from 'node:assert/strict'
import test from 'node:test'

import { calculatePosterImagePlacement, normalizePosterBox } from '../posterCanvasPreview.js'

test('normalizes the default poster box to the full canvas', () => {
  assert.deepEqual(normalizePosterBox(null, 1080, 1440), {
    left: 0,
    top: 0,
    width: 1080,
    height: 1440
  })
})

test('matches backend cover placement for a landscape background', () => {
  assert.deepEqual(calculatePosterImagePlacement({
    imageWidth: 1600,
    imageHeight: 900,
    canvasWidth: 1080,
    canvasHeight: 1440,
    transform: { fit: 'cover', scale: 1, focal_x: 0.5, focal_y: 0.5 }
  }), {
    left: 0,
    top: 0,
    width: 1080,
    height: 1440,
    imageX: -740,
    imageY: 0,
    imageWidth: 2560,
    imageHeight: 1440
  })
})

test('applies normalized product bounds and offsets', () => {
  const placement = calculatePosterImagePlacement({
    imageWidth: 1000,
    imageHeight: 1000,
    canvasWidth: 1080,
    canvasHeight: 1440,
    box: { x: 0.1, y: 0.25, width: 0.8, height: 0.5 },
    transform: { fit: 'contain', scale: 1, focal_x: 0.5, focal_y: 0.5, x_offset: 0.1 }
  })
  assert.deepEqual(placement, {
    left: 108,
    top: 360,
    width: 864,
    height: 720,
    imageX: 266,
    imageY: 360,
    imageWidth: 720,
    imageHeight: 720
  })
})
