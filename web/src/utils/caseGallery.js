export function isEnterpriseCaseGallery(gallery, parent = null) {
  if (!gallery) return false
  const root = gallery.parent_id ? parent : gallery
  return (
    gallery.visibility === 'enterprise'
    && root?.visibility === 'enterprise'
    && (root.image_design_role === 'reference' || root.name === '案例图库')
  )
}

export function filterCaseGalleryChildren(children, designStyle) {
  if (!designStyle) return children
  return children.filter((item) => item.design_style === designStyle)
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
