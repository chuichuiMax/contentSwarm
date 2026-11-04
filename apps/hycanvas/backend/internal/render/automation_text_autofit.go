package render

import (
	"image"
	"strings"
	"unicode"

	"golang.org/x/image/font"
)

const automationSingleLineKey = "contentswarmSingleLineAutoFit"

func shouldAutomationSingleLineAutoFit(node map[string]any) bool {
	if textNodeUsesTemplateTypography(node) {
		return false
	}
	return asBool(asObj(node["data"])[automationSingleLineKey])
}

func automationSingleLineText(node map[string]any) string {
	var builder strings.Builder
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			builder.WriteString(runText(asObj(runRaw), asObj(asObj(runRaw)["style"])))
		}
	}
	return strings.TrimSpace(strings.ReplaceAll(strings.ReplaceAll(builder.String(), "\n", ""), "\r", ""))
}

type styledGlyph struct {
	r     rune
	style map[string]any
}

func styledGlyphsFromNode(node map[string]any) []styledGlyph {
	out := make([]styledGlyph, 0, 16)
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			run := asObj(runRaw)
			style := asObj(run["style"])
			for _, r := range runText(run, style) {
				if r == '\n' || r == '\r' {
					continue
				}
				out = append(out, styledGlyph{r: r, style: style})
			}
		}
	}
	return out
}

func automationSingleLineLetterFit(
	node map[string]any,
	contentW, contentH float64,
	measure func(font.Face, map[string]any, string, float64) float64,
	runFace func(map[string]any) (font.Face, float64),
) {
	if contentW <= 0 || contentH <= 0 {
		return
	}
	if automationSingleLineText(node) == "" {
		return
	}
	tr := asObj(node["transform"])
	if tr != nil {
		if sx := asNum(tr["scaleX"]); sx != 0 && sx != 1 {
			tr["scaleX"] = 1.0
		}
	}
	setAllLetterSpacing(node, 0)
	if autoFit := asObj(asObj(node["box"])["autoFit"]); autoFit != nil {
		autoFit["enabled"] = false
	}
	totalAdv, lineH := measureDistributedLine(node, measure, runFace)
	if lineH > contentH*0.98 {
		return
	}
	if totalAdv > contentW*0.98 {
		expandTextBoxForWidth(node, totalAdv*1.04)
	}
}

func measureDistributedLine(
	node map[string]any,
	measure func(font.Face, map[string]any, string, float64) float64,
	runFace func(map[string]any) (font.Face, float64),
) (totalAdv, lineH float64) {
	for _, g := range styledGlyphsFromNode(node) {
		adv, h := glyphAdvance(g.r, g.style, measure, runFace)
		totalAdv += adv
		lineH = maxFloat(lineH, h)
	}
	return totalAdv, lineH
}

func glyphAdvance(
	r rune,
	style map[string]any,
	measure func(font.Face, map[string]any, string, float64) float64,
	runFace func(map[string]any) (font.Face, float64),
) (adv, lineH float64) {
	face, size := runFace(style)
	if face == nil {
		size = asNum(style["fontSize"])
		if size <= 0 {
			size = 16
		}
		return estimateRuneAdvance(r, size), runLineHeight(style, size)
	}
	defer face.Close()
	w := measure(face, style, string(r), 0)
	min := estimateRuneAdvance(r, size) * 1.15
	if w < min {
		w = min
	}
	return w, runLineHeight(style, size)
}

func estimateRuneAdvance(r rune, fontSize float64) float64 {
	if r <= unicode.MaxASCII {
		return fontSize * 0.55
	}
	return fontSize
}

// drawAutomationDistributedSingleLine paints one evenly spaced line (图二式
// 向左右均匀扩散): fixed font size, inter-glyph gap fills the box width.
func drawAutomationDistributedSingleLine(
	rc *rctx,
	m mat,
	node map[string]any,
	boxH, padL, padT, padB, contentW, contentH float64,
	layoutScale float64,
	measure func(font.Face, map[string]any, string, float64) float64,
	runFace func(map[string]any) (font.Face, float64),
) {
	glyphs := styledGlyphsFromNode(node)
	if len(glyphs) == 0 {
		return
	}
	type item struct {
		r     rune
		style map[string]any
		adv   float64
		lh    float64
	}
	items := make([]item, len(glyphs))
	totalAdv := 0.0
	lineH := 0.0
	for i, g := range glyphs {
		adv, lh := glyphAdvance(g.r, g.style, measure, runFace)
		items[i] = item{r: g.r, style: g.style, adv: adv, lh: lh}
		totalAdv += adv
		lineH = maxFloat(lineH, lh)
	}
	n := len(items)
	maxSize := 0.0
	for _, it := range items {
		if sz := asNum(it.style["fontSize"]); sz > maxSize {
			maxSize = sz
		}
	}
	if maxSize <= 0 {
		maxSize = 16
	}
	minGap := maxSize * 0.22
	for _, it := range items {
		if asStr(it.style["fontStyle"]) == "italic" {
			minGap *= 1.25
			break
		}
	}
	if n > 1 {
		needSpread := totalAdv + minGap*float64(n-1)
		if needSpread > contentW {
			expandTextBoxForWidth(node, needSpread*1.03)
			box := asObj(node["box"])
			pad := asObj(box["padding"])
			contentW = asNum(box["width"]) - asNum(pad["l"]) - asNum(pad["r"])
			if contentW < 0 {
				contentW = 0
			}
		}
	}
	gap := 0.0
	if n > 1 {
		gap = (contentW - totalAdv) / float64(n-1)
		if gap < minGap {
			needSpread := totalAdv + minGap*float64(n-1)
			expandTextBoxForWidth(node, needSpread*1.03)
			box := asObj(node["box"])
			pad := asObj(box["padding"])
			contentW = asNum(box["width"]) - asNum(pad["l"]) - asNum(pad["r"])
			gap = (contentW - totalAdv) / float64(n-1)
			if gap < minGap {
				gap = minGap
			}
		}
		if gap < 0 {
			gap = 0
		}
	}
	lineW := totalAdv + gap*float64(maxInt(0, n-1))
	x := padL + (contentW-lineW)/2
	if x < padL {
		x = padL
	}
	y := padT
	switch asStr(asObj(node["box"])["verticalAlign"]) {
	case "middle":
		y = padT + (contentH-lineH)/2 + lineH*0.82
	case "bottom":
		y = boxH - padB - lineH*0.15
	default:
		y = padT + lineH*0.82
	}

	hl := textHighlightOf(node)
	outline := textOutlineOf(node)
	if hl.ok {
		rc.drawTextHighlightBar(m, x, y, lineW, maxSize, hl)
	}
	for i, it := range items {
		face, size := runFace(it.style)
		if face == nil {
			x += it.adv
			if i < n-1 {
				x += gap
			}
			continue
		}
		col := pdfPaint(asObj(it.style["fill"]))
		if !col.ok {
			col = pdfColor{ok: true}
		}
		src := image.NewUniform(rasterColor(col, rc.alpha))
		fam := asStr(it.style["fontFamily"])
		wght := effectiveFontWeight(it.style)
		if size == 0 {
			size = 16
		}
		drawOne := func(r rune) {
			glyphFace := face
			if _, ok := face.GlyphAdvance(r); !ok {
				if alt := faceCovering(r, fam, wght, size*layoutScale, rc.font); alt != nil {
					glyphFace = alt
				} else {
					return
				}
			}
			rc.drawOutlinedGlyph(m, x, y, r, glyphFace, src, outline, layoutScale)
			if glyphFace != face {
				_ = glyphFace.Close()
			}
		}
		drawOne(it.r)
		_ = face.Close()
		x += it.adv
		if i < n-1 {
			x += gap
		}
	}
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func maxFloat(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}

func setAllLetterSpacing(node map[string]any, spacing float64) {
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			style := asObj(asObj(runRaw)["style"])
			if style != nil {
				style["letterSpacing"] = spacing
			}
		}
	}
}

func expandTextBoxForWidth(node map[string]any, needContent float64) {
	box := asObj(node["box"])
	if box == nil {
		return
	}
	pad := asObj(box["padding"])
	padL, padR := asNum(pad["l"]), asNum(pad["r"])
	current := asNum(box["width"]) - padL - padR
	if needContent <= current+0.5 {
		return
	}
	oldW := asNum(box["width"])
	newW := needContent + padL + padR
	delta := newW - oldW
	if delta <= 0 {
		return
	}
	box["width"] = newW
	tr := asObj(node["transform"])
	if tr == nil {
		tr = map[string]any{"x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0}
		node["transform"] = tr
	}
	tr["x"] = asNum(tr["x"]) - delta/2
}
