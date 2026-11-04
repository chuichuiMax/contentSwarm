package render

import (
	"image"
	"math"

	"golang.org/x/image/font"
	"golang.org/x/image/math/fixed"
)

type textHighlightSpec struct {
	padding float64
	radius  float64
	color   pdfColor
	alpha   float64
	ok      bool
}

type textOutlineSpec struct {
	width float64
	color pdfColor
	alpha float64
	ok    bool
}

func textEffectEnabled(raw map[string]any) bool {
	if raw == nil {
		return false
	}
	if v, ok := raw["enabled"].(bool); ok && !v {
		return false
	}
	return true
}

func textHighlightOf(node map[string]any) textHighlightSpec {
	arr, _ := node["textEffects"].([]any)
	for _, raw := range arr {
		eo := asObj(raw)
		if eo == nil || asStr(eo["kind"]) != "highlight" || !textEffectEnabled(eo) {
			continue
		}
		c, ca := shadowColor(asObj(eo["color"]))
		if ca <= 0 {
			continue
		}
		return textHighlightSpec{
			padding: asNum(eo["padding"]),
			radius:  asNum(eo["radius"]),
			color:   c,
			alpha:   ca,
			ok:      true,
		}
	}
	return textHighlightSpec{}
}

func textOutlineOf(node map[string]any) textOutlineSpec {
	if o := textOutlineFromEffects(node["textEffects"]); o.ok {
		return o
	}
	return textOutlineFromEffects(node["effects"])
}

func textOutlineFromEffects(raw any) textOutlineSpec {
	arr, _ := raw.([]any)
	for _, item := range arr {
		eo := asObj(item)
		if eo == nil || asStr(eo["kind"]) != "outline" || !textEffectEnabled(eo) {
			continue
		}
		c, ca := shadowColor(asObj(eo["color"]))
		if ca <= 0 {
			continue
		}
		w := asNum(eo["width"])
		if w <= 0 {
			w = 2
		}
		return textOutlineSpec{width: w, color: c, alpha: ca, ok: true}
	}
	return textOutlineSpec{}
}

func textNodeUsesTemplateTypography(node map[string]any) bool {
	if textHighlightOf(node).ok || textOutlineOf(node).ok {
		return true
	}
	return false
}

func (rc *rctx) drawTextHighlightBar(m mat, x, y, width, fontSize float64, hl textHighlightSpec) {
	if !hl.ok || width <= 0 || fontSize <= 0 {
		return
	}
	vpad := fontSize * 0.15
	pad := hl.padding
	if pad <= 0 {
		pad = 8
	}
	rx := x - pad
	ry := y - fontSize*0.82 - vpad
	rw := width + pad*2
	rh := fontSize + vpad*2
	col := rasterColor(hl.color, rc.alpha*hl.alpha)
	rc.fillPolyRect(m, rx, ry, rw, rh, col)
	_ = hl.radius
}

func (rc *rctx) drawOutlinedGlyph(
	m mat,
	x, y float64,
	r rune,
	face font.Face,
	fillSrc image.Image,
	outline textOutlineSpec,
	scale float64,
) {
	if !outline.ok {
		dx, dy := m.apply(x, y)
		d := &font.Drawer{Dst: rc.dst, Src: fillSrc, Face: face, Dot: fixed.P(int(math.Round(dx)), int(math.Round(dy)))}
		d.DrawString(string(r))
		return
	}
	hw := outline.width * scale * 0.5
	if hw < 0.5 {
		hw = 0.5
	}
	strokeCol := rasterColor(outline.color, rc.alpha*outline.alpha)
	strokeSrc := image.NewUniform(strokeCol)
	for _, off := range [][2]float64{
		{-hw, 0}, {hw, 0}, {0, -hw}, {0, hw},
		{-hw, -hw}, {hw, -hw}, {-hw, hw}, {hw, hw},
	} {
		dx, dy := m.apply(x+off[0]/scale, y+off[1]/scale)
		d := &font.Drawer{Dst: rc.dst, Src: strokeSrc, Face: face, Dot: fixed.P(int(math.Round(dx)), int(math.Round(dy)))}
		d.DrawString(string(r))
	}
	dx, dy := m.apply(x, y)
	d := &font.Drawer{Dst: rc.dst, Src: fillSrc, Face: face, Dot: fixed.P(int(math.Round(dx)), int(math.Round(dy)))}
	d.DrawString(string(r))
}
