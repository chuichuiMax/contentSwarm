package templates

import (
	"math"
	"strings"
	"unicode"

	"hycanvas/backend/internal/persistence"
)

// fitFilledTextNodes keeps replaced business-variable text on one line at the
// original font size: letter-spacing distributes glyphs evenly across the box
// width (no scaleX squeeze).
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
	fitTextNodeSingleLine(target)
	markAutomationSingleLineAutoFit(target)
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

func markAutomationSingleLineAutoFit(node map[string]any) {
	data := asObj(node["data"])
	if data == nil {
		data = map[string]any{}
		node["data"] = data
	}
	data["contentswarmSingleLineAutoFit"] = true
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
	if asStr(box["mode"]) == "autoWidth" {
		return
	}
	contentW, contentH := textBoxContentSize(box)
	if contentW <= 0 || contentH <= 0 {
		return
	}
	resetAutomationHorizontalScale(node)
	disableBoxAutoFit(node)
	naturalH := estimateBaseLineHeight(node, 1)
	if naturalH > contentH*0.98 {
		return
	}
	distributeSingleLineLetterSpacing(node, contentW)
	ensureSingleLineCenterAlign(node)
}

func disableBoxAutoFit(node map[string]any) {
	autoFit := asObj(asObj(node["box"])["autoFit"])
	if autoFit != nil {
		autoFit["enabled"] = false
	}
}

func resetAutomationHorizontalScale(node map[string]any) {
	tr := asObj(node["transform"])
	if tr == nil {
		return
	}
	if sx := asNum(tr["scaleX"]); sx != 0 && sx != 1 {
		tr["scaleX"] = 1.0
	}
}

func minInterGlyphGap(node map[string]any) float64 {
	gap := maxRunFontSize(node) * 0.22
	if nodeUsesItalic(node) {
		gap *= 1.25
	}
	return gap
}

func nodeUsesItalic(node map[string]any) bool {
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			style := asObj(asObj(runRaw)["style"])
			if style == nil {
				continue
			}
			if asStr(style["fontStyle"]) == "italic" {
				return true
			}
		}
	}
	return false
}

// PrepareCoverDesignTextSpread re-applies fact-line diffusion before PNG export
// so automation covers match the template preview (图二式均匀扩散).
func PrepareCoverDesignTextSpread(file map[string]any) {
	meta := asObj(file["meta"])
	if asStr(meta["kind"]) != persistence.DocKindContentSwarmCover {
		return
	}
	for _, pageRaw := range asArr(file["pages"]) {
		for _, root := range asArr(asObj(pageRaw)["children"]) {
			visitTree(asObj(root), func(node map[string]any) {
				if asStr(node["type"]) != "text" || !shouldSpreadCoverFactLine(node) {
					return
				}
				fitTextNodeSingleLine(node)
				markAutomationSingleLineAutoFit(node)
			})
		}
	}
}

func shouldSpreadCoverFactLine(node map[string]any) bool {
	if nodeHasStylisticTextEffects(node) {
		return false
	}
	if maxRunFontSize(node) > 40 {
		return false
	}
	if shouldAutomationSingleLineAutoFit(node) {
		return true
	}
	box := asObj(node["box"])
	if box == nil || asStr(box["mode"]) == "autoWidth" {
		return false
	}
	_, contentH := textBoxContentSize(box)
	if contentH > 88 {
		return false
	}
	glyphs := countVisibleGlyphs(node)
	if glyphs < 2 || glyphs > 12 {
		return false
	}
	if maxRunFontSize(node) > 100 {
		return false
	}
	return true
}

func shouldAutomationSingleLineAutoFit(node map[string]any) bool {
	v, _ := asObj(node["data"])["contentswarmSingleLineAutoFit"].(bool)
	return v
}

func distributeSingleLineLetterSpacing(node map[string]any, contentW float64) {
	baseW := estimateBaseLineWidth(node, 1)
	glyphs := countVisibleGlyphs(node)
	if glyphs <= 0 || baseW <= 0 {
		return
	}
	minGap := minInterGlyphGap(node)
	needSpread := baseW + minGap*float64(maxInt(0, glyphs-1))
	if needSpread > contentW {
		expandTextBoxToNaturalWidth(node, needSpread)
		contentW, _ = textBoxContentSize(asObj(node["box"]))
	}
	targetW := contentW * 0.98
	if glyphs == 1 {
		setLetterSpacingAbsolute(node, 0)
		return
	}
	gap := (targetW - baseW) / float64(glyphs-1)
	if gap < minGap {
		needSpread = baseW + minGap*float64(glyphs-1)
		expandTextBoxToNaturalWidth(node, needSpread*1.02)
		contentW, _ = textBoxContentSize(asObj(node["box"]))
		targetW = contentW * 0.98
		gap = (targetW - baseW) / float64(glyphs-1)
		if gap < minGap {
			gap = minGap
		}
	}
	if gap < 0 {
		gap = 0
	}
	ls := gap * float64(glyphs-1) / float64(glyphs)
	setLetterSpacingAbsolute(node, ls)
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func expandTextBoxToNaturalWidth(node map[string]any, baseW float64) {
	box := asObj(node["box"])
	if box == nil {
		return
	}
	padding := asObj(box["padding"])
	padL, padR := asNum(padding["l"]), asNum(padding["r"])
	needContent := baseW * 1.02
	currentContent, _ := textBoxContentSize(box)
	if needContent <= currentContent+0.5 {
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

func setLetterSpacingAbsolute(node map[string]any, spacing float64) {
	if spacing < 0 {
		spacing = 0
	}
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			style := asObj(asObj(runRaw)["style"])
			if style == nil {
				continue
			}
			style["letterSpacing"] = spacing
		}
	}
}

func ensureSingleLineCenterAlign(node map[string]any) {
	for _, paragraphRaw := range asArr(node["content"]) {
		paragraph := asObj(paragraphRaw)
		style := asObj(paragraph["style"])
		if style == nil {
			style = map[string]any{}
			paragraph["style"] = style
		}
		if asStr(style["align"]) == "" {
			style["align"] = "center"
		}
	}
}

func countVisibleGlyphs(node map[string]any) int {
	n := 0
	for _, paragraphRaw := range asArr(node["content"]) {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			text := strings.TrimSpace(asStr(asObj(runRaw)["text"]))
			for _, r := range text {
				if r == '\n' || r == '\r' {
					continue
				}
				n++
			}
		}
	}
	return n
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
				lineWidth += advance + letterSpacing
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
