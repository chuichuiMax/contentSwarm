const editorFontFaces = new Map()

function editorFontDescriptor(layer) {
  const serif = String(layer.font_family || '')
    .toLowerCase()
    .includes('serif')
  const bold = Number(layer.font_weight || 400) >= 600
  return {
    family: serif ? 'Noto Serif CJK SC' : 'Noto Sans CJK SC',
    weight: bold ? '700' : '400',
    key: `noto-${serif ? 'serif' : 'sans'}-cjk-${bold ? 'bold' : 'regular'}`
  }
}

export async function ensureEditorFonts(layers = []) {
  if (typeof window === 'undefined' || typeof window.FontFace !== 'function' || !document.fonts)
    return
  const descriptors = new Map(
    layers.map((layer) => {
      const descriptor = editorFontDescriptor(layer)
      return [`${descriptor.family}-${descriptor.weight}`, descriptor]
    })
  )
  await Promise.all(
    [...descriptors.values()].map(async ({ family, weight, key }) => {
      const cacheKey = `${family}-${weight}`
      if (!editorFontFaces.has(cacheKey)) {
        const face = new window.FontFace(family, `url(/api/content/covers/editor-fonts/${key})`, {
          style: 'normal',
          weight
        })
        editorFontFaces.set(
          cacheKey,
          face.load().then((loaded) => document.fonts.add(loaded))
        )
      }
      await editorFontFaces.get(cacheKey)
    })
  )
}

export function applyCanvasFont(context, layer) {
  const style = layer.font_style || 'normal'
  const weight = layer.font_weight || 400
  const size = layer.font_size || 64
  const family = editorFontDescriptor(layer).family
  context.font = `${style} ${weight} ${size}px "${family}", sans-serif`
  context.textBaseline = 'top'
}

export function measureSpacedLine(context, text, spacing = 0) {
  const characters = [...String(text || '')]
  return characters.reduce(
    (width, character, index) =>
      width + context.measureText(character).width + (index < characters.length - 1 ? spacing : 0),
    0
  )
}

function drawSpacedLine(
  context,
  text,
  x,
  y,
  spacing,
  method = 'fillText',
  fillRuns = [],
  sourceOffset = 0,
  sourceLength = 0,
  defaultFill = ''
) {
  let cursor = x
  const characters = [...String(text || '')]
  characters.forEach((character, index) => {
    if (method === 'fillText' && fillRuns.length && sourceLength > 0) {
      const runLength = Math.max(...fillRuns.map((item) => Number(item.end) || 0))
      const sourceIndex = Math.min(
        runLength - 1,
        Math.floor(((sourceOffset + index) * runLength) / sourceLength)
      )
      context.fillStyle =
        fillRuns.find((item) => Number(item.start) <= sourceIndex && sourceIndex < Number(item.end))
          ?.fill || defaultFill
    }
    context[method](character, cursor, y)
    cursor += context.measureText(character).width
    if (index < characters.length - 1) cursor += spacing
  })
}

function wrapText(context, text, width, spacing, maxLines) {
  const lines = []
  for (const paragraph of String(text || '').split('\n')) {
    let current = ''
    for (const character of paragraph) {
      const candidate = `${current}${character}`
      if (current && measureSpacedLine(context, candidate, spacing) > width) {
        lines.push(current)
        current = character
      } else {
        current = candidate
      }
      if (lines.length >= maxLines) break
    }
    if (lines.length >= maxLines) break
    lines.push(current)
  }
  const visibleLines = lines.slice(0, maxLines)
  const source = String(text || '').replaceAll('\n', '')
  if (visibleLines.join('').length < source.length && visibleLines.length) {
    let last = visibleLines.at(-1)
    while (last && measureSpacedLine(context, `${last}…`, spacing) > width) last = last.slice(0, -1)
    visibleLines[visibleLines.length - 1] = `${last}…`
  }
  return visibleLines
}

function roundedRect(context, x, y, width, height, radius) {
  const safeRadius = Math.max(0, Math.min(radius, width / 2, height / 2))
  context.beginPath()
  context.roundRect(x, y, width, height, safeRadius)
  context.fill()
}

export function drawTextLayer(context, layer) {
  if (!layer.visible || !layer.text) return
  context.save()
  context.globalAlpha = Number(layer.opacity ?? 1)
  context.translate(layer.x + layer.width / 2, layer.y + layer.height / 2)
  context.rotate(((Number(layer.rotation) || 0) * Math.PI) / 180)
  context.translate(-layer.width / 2, -layer.height / 2)

  if (layer.background_fill) {
    const padding = Number(layer.background_padding) || 0
    context.save()
    context.globalAlpha *= Number(layer.background_opacity ?? 1)
    context.fillStyle = layer.background_fill
    roundedRect(
      context,
      -padding,
      -padding,
      layer.width + padding * 2,
      layer.height + padding * 2,
      Number(layer.background_radius) || 0
    )
    context.restore()
  }

  let effectiveFontSize = Number(layer.font_size) || 64
  applyCanvasFont(context, layer)
  if (!String(layer.text).includes('\n')) {
    const characters = [...String(layer.text)]
    const spacingWidth = Math.max(0, characters.length - 1) * (Number(layer.letter_spacing) || 0)
    const glyphWidth = measureSpacedLine(context, layer.text, 0)
    const availableGlyphWidth = Math.max(0, layer.width - spacingWidth)
    if (glyphWidth > availableGlyphWidth) {
      effectiveFontSize = Math.max(8, effectiveFontSize * (availableGlyphWidth / glyphWidth))
      applyCanvasFont(context, { ...layer, font_size: effectiveFontSize })
    }
  }
  context.fillStyle = layer.fill
  context.strokeStyle = layer.stroke_color
  context.lineWidth = layer.stroke ? Number(layer.stroke_width) || 0 : 0
  context.lineJoin = 'round'
  if (layer.shadow) {
    context.shadowColor = layer.shadow_color
    context.shadowBlur = Number(layer.shadow_blur) || 0
    context.shadowOffsetX = Number(layer.shadow_offset_x) || 0
    context.shadowOffsetY = Number(layer.shadow_offset_y) || 0
  }
  const lineAdvance = effectiveFontSize * (Number(layer.line_height) || 1.2)
  const maxLines = Math.max(1, Math.floor(layer.height / lineAdvance))
  const lines = wrapText(
    context,
    layer.text,
    layer.width,
    Number(layer.letter_spacing) || 0,
    maxLines
  )
  const blockHeight = Math.min(layer.height, lines.length * lineAdvance)
  let y = Math.max(0, (layer.height - blockHeight) / 2)
  const sourceLength = [...String(layer.text || '').replaceAll('\n', '')].length
  let sourceOffset = 0
  lines.forEach((line) => {
    const lineWidth = measureSpacedLine(context, line, Number(layer.letter_spacing) || 0)
    const x =
      layer.align === 'left'
        ? 0
        : layer.align === 'right'
          ? layer.width - lineWidth
          : (layer.width - lineWidth) / 2
    if (layer.stroke && Number(layer.stroke_width) > 0) {
      drawSpacedLine(context, line, x, y, Number(layer.letter_spacing) || 0, 'strokeText')
    }
    drawSpacedLine(
      context,
      line,
      x,
      y,
      Number(layer.letter_spacing) || 0,
      'fillText',
      layer.fill_runs || [],
      sourceOffset,
      sourceLength,
      layer.fill
    )
    sourceOffset += [...line].length
    y += lineAdvance
  })
  context.restore()
}

export function drawEditorScene(context, scene, background, selectedLayerId = '') {
  const { width, height, safe_area: safeArea = {} } = scene.canvas
  context.clearRect(0, 0, width, height)
  context.fillStyle = '#ffffff'
  context.fillRect(0, 0, width, height)
  if (background) context.drawImage(background, 0, 0, width, height)
  ;[...scene.layers]
    .sort((left, right) => left.order - right.order)
    .forEach((layer) => {
      drawTextLayer(context, layer)
    })

  if (safeArea.width && safeArea.height) {
    context.save()
    context.strokeStyle = 'rgba(255, 255, 255, 0.48)'
    context.setLineDash([10, 8])
    context.lineWidth = 2
    context.strokeRect(
      safeArea.x * width,
      safeArea.y * height,
      safeArea.width * width,
      safeArea.height * height
    )
    context.restore()
  }

  const selected = scene.layers.find((layer) => layer.id === selectedLayerId && layer.visible)
  if (!selected) return
  context.save()
  context.translate(selected.x + selected.width / 2, selected.y + selected.height / 2)
  context.rotate(((Number(selected.rotation) || 0) * Math.PI) / 180)
  context.strokeStyle = '#315efb'
  context.lineWidth = 3
  context.setLineDash([8, 6])
  context.strokeRect(-selected.width / 2, -selected.height / 2, selected.width, selected.height)
  context.setLineDash([])
  context.fillStyle = '#ffffff'
  context.strokeStyle = '#315efb'
  context.beginPath()
  context.rect(selected.width / 2 - 10, selected.height / 2 - 10, 20, 20)
  context.fill()
  context.stroke()
  context.restore()
}

export function pointInLayer(layer, x, y) {
  const centerX = layer.x + layer.width / 2
  const centerY = layer.y + layer.height / 2
  const radians = (-(Number(layer.rotation) || 0) * Math.PI) / 180
  const dx = x - centerX
  const dy = y - centerY
  const localX = dx * Math.cos(radians) - dy * Math.sin(radians)
  const localY = dx * Math.sin(radians) + dy * Math.cos(radians)
  return Math.abs(localX) <= layer.width / 2 && Math.abs(localY) <= layer.height / 2
}
