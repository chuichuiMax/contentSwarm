package templates

import (
	"math"
	"strings"
	"unicode"
)

// fitFilledTextNodes keeps replaced business-variable text inside its box.
// Line height, letter spacing, alignment, and position stay as designed in the
// template. Only a line that would overflow is scaled down with its font.
func fitFilledTextNodes(file map[string]any, declarations []any, filledNodeIDs map[string]struct{}) {
	roles := semanticRoleByNodeID(declarations)
	seen := make(map[string]struct{}, len(filledNodeIDs)+8)
	for id := range filledNodeIDs {
		seen[id] = struct{}{}
		applySingleLineFitToNode(file, id, roles[id])
	}
	for _, raw := range declarations {
		field := asObj(raw)
		if asStr(field["kind"]) != "text" {
			continue
		}
		role := asStr(field["semanticRole"])
		label := strings.TrimSpace(asStr(field["label"]))
		if !isFactTemplateRole(role) && !isSpreadFieldLabel(label) {
			continue
		}
		id := asStr(field["nodeId"])
		if id == "" {
			continue
		}
		if _, ok := seen[id]; ok {
			continue
		}
		seen[id] = struct{}{}
		applySingleLineFitToNode(file, id, role)
	}
}

func nodeHasStylisticTextEffects(node map[string]any) bool {
	arr, _ := node["textEffects"].([]any)
	for _, raw := range arr {
		eo := asObj(raw)
		if eo == nil {
			continue
		}
		if v, ok := eo["enabled"].(bool); ok && !v {
			continue
		}
		switch asStr(eo["kind"]) {
		case "highlight", "outline", "shadow", "glow", "neon", "hollow":
			return true
		}
	}
	return false
}

func applySingleLineFitToNode(file map[string]any, nodeID, role string) {
	if isNarrativeSemanticRole(role) {
		return
	}
	var target map[string]any
	for _, pageRaw := range asArr(file["pages"]) {
		for _, root := range asArr(asObj(pageRaw)["children"]) {
			visitTree(asObj(root), func(node map[string]any) {
				if asStr(node["id"]) == nodeID && asStr(node["type"]) == "text" {
					target = node
				}
			})
		}
	}
	if target == nil {
		return
	}
	if nodeHasStylisticTextEffects(target) {
		return
	}
	if strings.TrimSpace(collectNodeText(target)) == "" {
		return
	}
	shrinkNarrativeToSingleLine(target)
}

func collectNodeText(node map[string]any) string {
	var b strings.Builder
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			b.WriteString(asStr(asObj(runRaw)["text"]))
		}
	}
	return b.String()
}

func isSpreadFieldLabel(label string) bool {
	switch label {
	case "岗位", "从业年限", "年限", "设计师自荐", "目标人群", "设计风格", "所在区域", "工艺类型", "户型":
		return true
	default:
		return false
	}
}

func isFactTemplateRole(role string) bool {
	switch role {
	case "project_name", "project_name_en", "project_area", "designer", "completion_year",
		"brand_name", "design_style", "layout", "service_region", "target_audience",
		"process_type", "service_years", "job_position":
		return true
	default:
		return false
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

// PrepareCoverDesignTextSpread used to restretch fact text before PNG export.
// That changed letter spacing, line box, and alignment relative to the cover
// shown in the editor, so export now keeps the template typography.
func PrepareCoverDesignTextSpread(file map[string]any) {
	_ = file
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

func estimateBaseLineWidth(node map[string]any, fontScale float64) float64 {
	w, _ := estimateSingleLineLayout(node, fontScale, 0)
	return w
}

func estimateBaseLineHeight(node map[string]any, fontScale float64) float64 {
	_, h := estimateSingleLineLayout(node, fontScale, 0)
	return h
}

func estimateSingleLineLayout(node map[string]any, fontScale float64, letterSpacing float64) (width, height float64) {
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
			text := asStr(run["text"])
			for _, r := range text {
				if r == '\n' || r == '\r' {
					continue
				}
				advance := fontSize * 1.12
				if r <= unicode.MaxASCII {
					advance = fontSize * 0.55
				}
				lineWidth += advance + asNum(style["letterSpacing"]) + letterSpacing
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
