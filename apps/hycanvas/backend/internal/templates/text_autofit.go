package templates

import (
	"math"
	"strings"
	"unicode"
)

// fitFilledTextNodes shrinks fonts so replaced business-variable text stays on
// one line inside the template box (no wrap), matching editor auto-fit behavior.
func fitFilledTextNodes(file map[string]any, declarations []any, filledNodeIDs map[string]struct{}) {
	if len(filledNodeIDs) == 0 {
		return
	}
	roles := semanticRoleByNodeID(declarations)
	for _, pageRaw := range asArr(file["pages"]) {
		for _, root := range asArr(asObj(pageRaw)["children"]) {
			visitTree(asObj(root), func(node map[string]any) {
				id := asStr(node["id"])
				if _, ok := filledNodeIDs[id]; !ok || asStr(node["type"]) != "text" {
					return
				}
				role := roles[id]
				if role == "label" || isNarrativeSemanticRole(role) {
					return
				}
				fitTextNodeSingleLine(node)
			})
		}
	}
}

func semanticRoleByNodeID(declarations []any) map[string]string {
	out := make(map[string]string, len(declarations))
	for _, raw := range declarations {
		field := asObj(raw)
		if asStr(field["kind"]) != "text" {
			continue
		}
		out[asStr(field["nodeId"])] = asStr(field["semanticRole"])
	}
	return out
}

func normalizeFilledText(value string) string {
	value = strings.ReplaceAll(value, "\r\n", "")
	value = strings.ReplaceAll(value, "\n", "")
	value = strings.ReplaceAll(value, "\r", "")
	return strings.TrimSpace(value)
}

func fitTextNodeSingleLine(node map[string]any) {
	box := asObj(node["box"])
	if box == nil {
		return
	}
	mode := asStr(box["mode"])
	if mode == "autoWidth" {
		return
	}
	contentW, contentH := textBoxContentSize(box)
	if contentW <= 0 || contentH <= 0 {
		return
	}
	autoFit := asObj(box["autoFit"])
	minFont := asNum(autoFit["min"])
	if minFont <= 0 {
		minFont = 8
	}
	maxBase := maxRunFontSize(node)
	if maxBase <= 0 {
		maxBase = 16
	}
	minScale := minFont / maxBase
	if minScale > 1 {
		minScale = 1
	}

	fits := func(scale float64) bool {
		w, h := estimateSingleLineLayout(node, scale)
		return w <= contentW+0.5 && h <= contentH+0.5
	}
	if fits(1) {
		return
	}
	if !fits(minScale) {
		scaleTextNodeFonts(node, minScale)
		return
	}
	lo, hi := minScale, 1.0
	for step := 0; step < 12; step++ {
		mid := (lo + hi) / 2
		if fits(mid) {
			lo = mid
		} else {
			hi = mid
		}
	}
	scaleTextNodeFonts(node, lo)
}

func textBoxContentSize(box map[string]any) (width, height float64) {
	padding := asObj(box["padding"])
	width = asNum(box["width"]) - asNum(padding["l"]) - asNum(padding["r"])
	height = asNum(box["height"]) - asNum(padding["t"]) - asNum(padding["b"])
	if width < 0 {
		width = 0
	}
	if height < 0 {
		height = 0
	}
	return width, height
}

func maxRunFontSize(node map[string]any) float64 {
	maxSize := 0.0
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			size := asNum(asObj(asObj(runRaw)["style"])["fontSize"])
			if size > maxSize {
				maxSize = size
			}
		}
	}
	return maxSize
}

func scaleTextNodeFonts(node map[string]any, scale float64) {
	if scale >= 0.999 && scale <= 1.001 {
		return
	}
	for _, paragraphRaw := range asArr(node["content"]) {
		paragraph := asObj(paragraphRaw)
		for _, runRaw := range asArr(paragraph["runs"]) {
			run := asObj(runRaw)
			style := asObj(run["style"])
			if style == nil {
				continue
			}
			size := asNum(style["fontSize"])
			if size > 0 {
				style["fontSize"] = size * scale
			}
			lineHeight := asObj(style["lineHeight"])
			if asStr(lineHeight["mode"]) == "absolute" {
				if value := asNum(lineHeight["value"]); value > 0 {
					lineHeight["value"] = value * scale
				}
			}
		}
	}
}

func estimateSingleLineLayout(node map[string]any, fontScale float64) (width, height float64) {
	lineWidth, lineHeight := 0.0, 0.0
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			run := asObj(runRaw)
			style := asObj(run["style"])
			fontSize := asNum(style["fontSize"]) * fontScale
			if fontSize <= 0 {
				fontSize = 16 * fontScale
			}
			runHeight := estimatedLineHeight(style, fontSize)
			lineHeight = math.Max(lineHeight, runHeight)
			letterSpacing := asNum(style["letterSpacing"])
			for _, chunk := range templateWrapChunks(asStr(run["text"])) {
				for _, r := range chunk.text {
					advance := fontSize
					if r == '\n' || r == '\r' {
						continue
					}
					if r <= unicode.MaxASCII {
						advance = fontSize * 0.55
					}
					lineWidth += advance + letterSpacing
				}
			}
		}
	}
	if lineHeight == 0 {
		lineHeight = 16 * 1.2 * fontScale
	}
	return lineWidth, lineHeight
}

func isNarrativeSemanticRole(role string) bool {
	switch role {
	case "title", "subtitle", "body_excerpt":
		return true
	default:
		return false
	}
}
