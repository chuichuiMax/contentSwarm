import assert from 'node:assert/strict'
import test from 'node:test'

import {
  TRAFFIC_COLUMN_DEFAULTS,
  moveColumnSetting,
  normalizeColumnSettings
} from '../trafficColumnSettings.js'

test('column settings keep known columns and drop unknown ones', () => {
  const settings = normalizeColumnSettings([
    { key: 'current_branch', title: '改名也不会生效', visible: false, width: '180', sortable: true, fixed: true },
    { key: 'missing', visible: true },
    { key: 'name', visible: true, width: '12' }
  ])

  assert.equal(settings[0].key, 'current_branch')
  assert.equal(settings[0].title, '所属门店')
  assert.equal(settings[0].visible, false)
  assert.equal(settings[0].width, '180')
  assert.equal(settings[0].sortable, true)
  assert.equal(settings[0].fixed, true)
  assert.equal(settings[1].key, 'name')
  assert.equal(settings[1].width, 'auto')
  assert.equal(settings.length, TRAFFIC_COLUMN_DEFAULTS.length)
  assert.ok(settings.some((item) => item.key === 'case_share_count'))
})

test('column settings fall back to the default order', () => {
  assert.deepEqual(
    normalizeColumnSettings(null).map((item) => item.key),
    TRAFFIC_COLUMN_DEFAULTS.map((item) => item.key)
  )
})

test('column settings can be reordered', () => {
  const moved = moveColumnSetting(TRAFFIC_COLUMN_DEFAULTS, 0, 2)
  assert.deepEqual(moved.slice(0, 3).map((item) => item.key), ['name', 'login_account', 'index'])
  assert.equal(TRAFFIC_COLUMN_DEFAULTS[0].key, 'index')
})
