export const MATERIAL_UPLOAD_MAX_EDGE = 8192
export const MATERIAL_UPLOAD_MAX_PIXELS = 40_000_000
export const MATERIAL_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
export const MATERIAL_UPLOAD_WEBP_QUALITY = 0.8

export function scaledImageSize(width, height) {
  if (width < 2 || height < 2) {
    throw new Error('图片尺寸过小，无法上传')
  }
  let scale = 1
  const longest = Math.max(width, height)
  if (longest > MATERIAL_UPLOAD_MAX_EDGE) {
    scale = Math.min(scale, MATERIAL_UPLOAD_MAX_EDGE / longest)
  }
  if (width * height > MATERIAL_UPLOAD_MAX_PIXELS) {
    scale = Math.min(scale, Math.sqrt(MATERIAL_UPLOAD_MAX_PIXELS / (width * height)))
  }
  if (scale >= 1) return { width, height }
  return {
    width: Math.max(2, Math.floor(width * scale)),
    height: Math.max(2, Math.floor(height * scale))
  }
}

export function webpUploadName(fileName) {
  const base = String(fileName || 'image').split(/[/\\]/).pop()
  const dot = base.lastIndexOf('.')
  const stem = dot > 0 ? base.slice(0, dot) : base
  return `${stem || 'image'}.webp`
}

async function decodeImage(file) {
  try {
    return await createImageBitmap(file, { imageOrientation: 'from-image' })
  } catch {
    return createImageBitmap(file)
  }
}

export async function encodeFileAsWebp(file) {
  if (typeof createImageBitmap !== 'function' || typeof document === 'undefined') return file
  let bitmap
  try {
    bitmap = await decodeImage(file)
  } catch {
    return file
  }
  try {
    const size = scaledImageSize(bitmap.width, bitmap.height)
    const alreadyWebp = file.type === 'image/webp' || /\.webp$/i.test(file.name || '')
    if (alreadyWebp && size.width === bitmap.width && size.height === bitmap.height) return file
    const canvas = document.createElement('canvas')
    canvas.width = size.width
    canvas.height = size.height
    const context = canvas.getContext('2d', { alpha: true })
    if (!context) return file
    context.drawImage(bitmap, 0, 0, size.width, size.height)
    const blob = await new Promise((resolve) => {
      canvas.toBlob((result) => resolve(result), 'image/webp', MATERIAL_UPLOAD_WEBP_QUALITY)
    })
    if (!blob || !String(blob.type || '').includes('webp')) return file
    if (blob.size > MATERIAL_UPLOAD_MAX_BYTES) {
      throw new Error(`「${file.name}」转成 WebP 后仍超过 100 MB`)
    }
    return new File([blob], webpUploadName(file.name), {
      type: 'image/webp',
      lastModified: file.lastModified
    })
  } finally {
    bitmap.close?.()
  }
}
