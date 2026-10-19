export function isEnterpriseCaseGallery(gallery, parent = null) {
  if (!gallery) return false
  const root = gallery.parent_id ? parent : gallery
  return (
    gallery.visibility === 'enterprise'
    && root?.visibility === 'enterprise'
    && (root.image_design_role === 'reference' || root.name === '案例图库')
  )
}

export function filterCaseGalleryChildren(children, designStyle, address = '') {
  const keyword = address.trim().toLowerCase()
  return children.filter((item) => {
    if (designStyle && item.design_style !== designStyle) return false
    if (!keyword) return true
    const building = `${item.building_name || ''} ${item.name || ''}`.toLowerCase()
    return building.includes(keyword)
  })
}

export function caseGalleryFolderLabel(gallery) {
  const building = (gallery?.building_name || '').trim()
  const style = (gallery?.design_style || '').trim()
  const area = (gallery?.area || '').trim()
  const areaText = area ? (area.endsWith('㎡') || area.endsWith('m²') ? area : `${area}㎡`) : ''
  if (building && style && areaText) return `${building}·${style}·${areaText}`
  return gallery?.name || ''
}

export function materialGalleryListLabel(gallery) {
  if (!gallery?.name) return ''
  return gallery.visibility === 'enterprise' ? `企业共享·${gallery.name}` : gallery.name
}

export function isCaseGalleryChild(gallery, galleries) {
  if (!gallery?.parent_id) return false
  const parent = galleries.find((item) => item.id === gallery.parent_id)
  return isEnterpriseCaseGallery(parent)
}

export function matchesCaseGalleryStyleFilter(gallery, galleries, designStyle) {
  if (!designStyle || !gallery) return true
  if (!isCaseGalleryChild(gallery, galleries)) return true
  return gallery.design_style === designStyle
}
