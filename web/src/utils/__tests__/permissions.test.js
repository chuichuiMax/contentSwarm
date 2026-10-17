import assert from 'node:assert/strict'
import test from 'node:test'

import { canAccessScopedPath, homePathForGrants, isRoughUploadOnly } from '../permissions.js'

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

test('folder grants override the legacy rough-only material library', () => {
  const grants = [
    'material_rough.upload',
    'material_folder.case-root.view',
    'material_folder.case-root.upload',
    'image_design.access'
  ]
  assert.equal(isRoughUploadOnly(true, grants), false)
  assert.equal(isRoughUploadOnly(true, ['material_rough.upload']), true)
  assert.equal(canAccessScopedPath(true, grants, '/materials/images'), true)
})

test('folder-level material grants open the material library routes', () => {
  const grants = ['material_folder.enterprise-initial-rough.view']
  assert.equal(homePathForGrants(true, grants), '/materials/images')
  assert.equal(canAccessScopedPath(true, grants, '/materials/images'), true)
  assert.equal(canAccessScopedPath(true, grants, '/content/new'), false)
})
