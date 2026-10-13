import assert from 'node:assert/strict'
import test from 'node:test'

import { scaledImageSize, webpUploadName } from '../materialUploadImage.js'

test('upload name keeps the original stem and uses webp', () => {
  assert.equal(webpUploadName('客厅.jpg'), '客厅.webp')
  assert.equal(webpUploadName('folder/room.JPEG'), 'room.webp')
  assert.equal(webpUploadName('already.webp'), 'already.webp')
})

test('large photos are scaled to the storage edge and pixel limits', () => {
  assert.deepEqual(scaledImageSize(1600, 1000), { width: 1600, height: 1000 })
  assert.deepEqual(scaledImageSize(10000, 5000), { width: 8192, height: 4096 })
  const pixels = scaledImageSize(12000, 9000)
  assert.ok(pixels.width * pixels.height <= 40_000_000)
  assert.ok(Math.max(pixels.width, pixels.height) <= 8192)
})

test('tiny images are rejected before encoding', () => {
  assert.throws(() => scaledImageSize(1, 10), /尺寸过小/)
})
