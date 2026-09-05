const clamp = (value, minimum, maximum) => Math.max(minimum, Math.min(maximum, Number(value)))

export function normalizePosterBox(box, canvasWidth, canvasHeight) {
  const source = box || { x: 0, y: 0, width: 1, height: 1 }
  const left = Math.round(clamp(source.x ?? 0, 0, 1) * canvasWidth)
  const top = Math.round(clamp(source.y ?? 0, 0, 1) * canvasHeight)
  const right = Math.round(clamp((source.x ?? 0) + (source.width ?? 1), 0, 1) * canvasWidth)
  const bottom = Math.round(clamp((source.y ?? 0) + (source.height ?? 1), 0, 1) * canvasHeight)
  return {
    left,
    top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top)
  }
}

export function calculatePosterImagePlacement({
  imageWidth,
  imageHeight,
  canvasWidth,
  canvasHeight,
  box,
  transform = {}
}) {
  const target = normalizePosterBox(box, canvasWidth, canvasHeight)
  const fit = transform.fit === 'contain' ? 'contain' : 'cover'
  const baseScale = fit === 'cover'
    ? Math.max(target.width / imageWidth, target.height / imageHeight)
    : Math.min(target.width / imageWidth, target.height / imageHeight)
  const scale = clamp(transform.scale ?? 1, 0.5, 2)
  const width = Math.max(1, Math.round(imageWidth * baseScale * scale))
  const height = Math.max(1, Math.round(imageHeight * baseScale * scale))
  const focalX = clamp(transform.focal_x ?? 0.5, 0, 1)
  const focalY = clamp(transform.focal_y ?? 0.5, 0, 1)
  const xOffset = clamp(transform.x_offset ?? 0, -0.5, 0.5)
  const yOffset = clamp(transform.y_offset ?? 0, -0.5, 0.5)
  return {
    ...target,
    imageX: Math.round(target.left + target.width * (0.5 + xOffset) - width * focalX),
    imageY: Math.round(target.top + target.height * (0.5 + yOffset) - height * focalY),
    imageWidth: width,
    imageHeight: height
  }
}
