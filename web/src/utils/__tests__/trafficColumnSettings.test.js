import assert from 'node:assert/strict'
import test from 'node:test'

import {
  TRAFFIC_COLUMN_DEFAULTS,
  TRAFFIC_COLUMN_SETTINGS_VERSION,
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

test('five metric columns enable sorting by default', () => {
  assert.deepEqual(
    normalizeColumnSettings(null).filter((column) => column.sortable).map((column) => column.key),
    ['decoration_count', 'review_notes_count', 'personal_generated_count', 'enterprise_generated_count', 'case_share_count']
  )
})

test('legacy settings gain metric sorting without losing custom layout or text sorting', () => {
  const settings = normalizeColumnSettings([
    { key: 'case_share_count', visible: true, width: '180', sortable: false, fixed: true },
    { key: 'name', visible: false, width: '90', sortable: true, fixed: false }
  ])
  assert.deepEqual(settings[0], {
    key: 'case_share_count', title: '案例分享', visible: true, width: '180', sortable: true, fixed: true
  })
  assert.equal(settings[1].visible, false)
  assert.equal(settings[1].sortable, true)
  assert.equal(settings[1].width, '90')
})

test('versioned settings preserve a metric sort switch explicitly turned off', () => {
  const saved = {
    version: TRAFFIC_COLUMN_SETTINGS_VERSION,
    columns: [{ key: 'case_share_count', visible: true, width: '180', sortable: false, fixed: true }]
  }
  const settings = normalizeColumnSettings(saved)
  assert.equal(settings[0].sortable, false)
  assert.equal(settings[0].fixed, true)
  assert.equal(settings.find((column) => column.key === 'decoration_count').sortable, true)
  assert.deepEqual(normalizeColumnSettings({ ...saved, columns: settings }), settings)
})
