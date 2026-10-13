import assert from 'node:assert/strict'
import test from 'node:test'

import { canAccessScopedPath, homePathForGrants } from '../permissions.js'

test('designer rough upload lands on the material library and cannot open other menus', () => {
  const grants = ['material_rough.upload']
  assert.equal(homePathForGrants(true, grants), '/materials/images')
  assert.equal(canAccessScopedPath(true, grants, '/materials/images'), true)
  assert.equal(canAccessScopedPath(true, grants, '/hycanvas'), false)
  assert.equal(canAccessScopedPath(true, grants, '/content/new'), false)
  assert.equal(canAccessScopedPath(true, grants, '/content/image-design'), false)
  assert.equal(canAccessScopedPath(true, grants, '/agent'), false)
})

test('designer image design permission opens only the image design page', () => {
  const grants = ['material_rough.upload', 'image_design.access']
  assert.equal(canAccessScopedPath(true, grants, '/content/image-design'), true)
  assert.equal(canAccessScopedPath(true, grants, '/materials/images'), true)
  assert.equal(canAccessScopedPath(true, grants, '/hycanvas'), false)
  assert.equal(canAccessScopedPath(true, grants, '/content/new'), false)
})

test('roles without configured grants keep the existing home', () => {
  assert.equal(homePathForGrants(false, []), '/agent')
  assert.equal(canAccessScopedPath(false, [], '/content/new'), true)
})
