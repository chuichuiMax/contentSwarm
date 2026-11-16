import assert from 'node:assert/strict'
import test from 'node:test'

import { requirementListShareUrl, requirementListUrl } from '../requirementListUrl.js'

test('requirement list url uses the current employee code', () => {
  assert.equal(
    requirementListUrl('H04854'),
    'http://portal.hi-run.net/edc/formview/z66223?usercode=H04854#/?_key=37n795'
  )
})

test('requirement list fill url omits the form key', () => {
  assert.equal(
    requirementListUrl('H04854', 'fill'),
    'http://portal.hi-run.net/edc/formview/z66223?usercode=H04854#/?'
  )
})

test('requirement list share url opens the public form for that employee', () => {
  assert.equal(
    requirementListShareUrl('H04854', '毛坯房', 'http://127.0.0.1:5173'),
    'http://127.0.0.1:5173/requirement-list/fill?type=%E6%AF%9B%E5%9D%AF%E6%88%BF&usercode=H04854'
  )
  assert.equal(requirementListShareUrl('', '毛坯房', 'http://127.0.0.1:5173'), '')
  assert.equal(requirementListShareUrl('H04854', '其他', 'http://127.0.0.1:5173'), '')
})

test('requirement list url is empty without an employee code', () => {
  assert.equal(requirementListUrl(''), '')
  assert.equal(requirementListUrl('  '), '')
})
