const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, Number(value)))

export function isLowConfidenceLayer(layer) {
  if (
    layer?.review_state === 'user_added' ||
    layer?.confidence === null ||
    layer?.confidence === undefined
  ) {
    return false
  }
  return Number(layer.confidence || 0) < 0.85 || Number(layer.consensus_count || 0) < 2
}

export function updateReviewBox(box, key, value) {
  const next = { ...box }
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return next
  if (key === 'x') next.x = clamp(numeric, 0, 1 - next.width)
  if (key === 'y') next.y = clamp(numeric, 0, 1 - next.height)
  if (key === 'width') next.width = clamp(numeric, 0.01, 1 - next.x)
  if (key === 'height') next.height = clamp(numeric, 0.01, 1 - next.y)
  return next
}

export function buildPosterReviewPayload(template, layers, confirm) {
  return {
    version: template.version,
    product_box: template.product_box,
    text_slots: layers,
    confirm: Boolean(confirm)
  }
}
