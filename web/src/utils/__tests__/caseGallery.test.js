import assert from 'node:assert/strict'
import test from 'node:test'

import {
  caseGalleryFolderLabel,
  filterCaseGalleryChildren,
  materialGalleryListLabel
} from '../caseGallery.js'

test('case folder label joins building, style and area', () => {
  assert.equal(
    caseGalleryFolderLabel({
      name: '旧名称',
      building_name: '桂语云峰',
      design_style: '复古风潮',
      area: '120'
    }),
    '桂语云峰·复古风潮·120㎡'
  )
  assert.equal(caseGalleryFolderLabel({ name: '毛坯房图库' }), '毛坯房图库')
})

test('enterprise galleries use the shared prefix in the side list', () => {
  assert.equal(
    materialGalleryListLabel({ name: '案例图库', visibility: 'enterprise' }),
    '企业共享·案例图库'
  )
  assert.equal(materialGalleryListLabel({ name: '我的上传', visibility: 'private' }), '我的上传')
})

test('case folders filter by style and building address together', () => {
  const children = [
    { name: '桂语云峰', building_name: '桂语云峰', design_style: '复古风潮' },
    { name: '洋湖天序', building_name: '洋湖天序', design_style: '复合写意' }
  ]
  assert.deepEqual(
    filterCaseGalleryChildren(children, '复古风潮', '桂语').map((item) => item.name),
    ['桂语云峰']
  )
  assert.equal(filterCaseGalleryChildren(children, '', '洋湖').length, 1)
})
