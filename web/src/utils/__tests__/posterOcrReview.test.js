import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildPosterReviewPayload,
  isLowConfidenceLayer,
  updateReviewBox
} from '../posterOcrReview.js'

test('flags weak OCR consensus but not a manually added layer', () => {
  assert.equal(
    isLowConfidenceLayer({ confidence: 0.92, consensus_count: 1, review_state: 'recognized' }),
    true
  )
  assert.equal(
    isLowConfidenceLayer({ confidence: 0.72, consensus_count: 1, review_state: 'user_added' }),
    false
  )
  assert.equal(
    isLowConfidenceLayer({ confidence: 0.96, consensus_count: 3, review_state: 'recognized' }),
    false
  )
})

test('keeps corrected OCR boxes inside the normalized canvas', () => {
  const source = { x: 0.2, y: 0.2, width: 0.6, height: 0.1 }
  assert.deepEqual(updateReviewBox(source, 'x', 0.9), { x: 0.4, y: 0.2, width: 0.6, height: 0.1 })
  assert.deepEqual(updateReviewBox(source, 'width', 0.95), {
    x: 0.2,
    y: 0.2,
    width: 0.8,
    height: 0.1
  })
})

test('builds an explicit versioned confirmation payload', () => {
  const layers = [{ id: 'slot-1', source_text: '确认文字' }]
  assert.deepEqual(
    buildPosterReviewPayload(
      { version: 3, product_box: { x: 0, y: 0, width: 1, height: 1 } },
      layers,
      true
    ),
    {
      version: 3,
      product_box: { x: 0, y: 0, width: 1, height: 1 },
      text_slots: layers,
      confirm: true
    }
  )
})
