package templates

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
	"unicode"
	"unicode/utf8"
)

type InstantiateImage struct {
	Filename    string
	ContentType string
	DataBase64  string
}

// InstantiateInput describes one automation-created design. Fields are keyed
// by the human-readable labels declared in the template's fillableFields.
type InstantiateInput struct {
	WorkspaceID      string
	Title            string
	Fields           map[string]string
	Images           map[string]InstantiateImage
	Background       *InstantiateImage
	PhotoComposition *PhotoComposition
}

// PreviewWithBackground applies the same ContentSwarm background transform as
// Instantiate without creating a design. The returned file is only rendered
// in memory by the HTTP preview endpoint.
func (s *Service) PreviewWithBackground(ctx context.Context, userID, templateID string, image InstantiateImage, compositions ...*PhotoComposition) (map[string]any, Template, error) {
	template, err := s.Get(ctx, userID, templateID)
	if err != nil {
		return nil, Template{}, err
	}
	file, err := s.GetFile(ctx, userID, templateID)
	if err != nil {
		return nil, Template{}, err
	}
	if len(compositions) > 0 && compositions[0] != nil {
		if err := applyPhotoComposition(file, compositions[0]); err != nil {
			return nil, Template{}, err
		}
	} else if err := applyBackgroundImage(file, image); err != nil {
		return nil, Template{}, err
	}
	return file, template, nil
}

// Instantiate fills a template's declared text fields and creates a decoupled
// design in the requested workspace.
func (s *Service) Instantiate(ctx context.Context, userID, templateID string, in InstantiateInput) (string, error) {
	if in.WorkspaceID == "" {
		return "", ErrBadRequest
	}
	if err := s.access.AssertMember(ctx, userID, in.WorkspaceID, "member"); err != nil {
		return "", ErrForbidden
	}

	var file map[string]any
	var template Template
	if seed, ok := findSeed(templateID); ok {
		_ = json.Unmarshal(seed.File, &file)
		template = seed.toTemplate()
	} else {
		row, err := s.getRow(ctx, templateID)
		if err != nil {
			return "", err
		}
		if !s.canSee(ctx, userID, row) {
			return "", ErrNotFound
		}
		_ = json.Unmarshal(row.File, &file)
		template = rowToTemplate(row)
	}

	if err := fillTextFields(file, template.FillableFields, in.Fields); err != nil {
		return "", err
	}
	if err := fillImageFields(file, template.FillableFields, in.Images); err != nil {
		return "", err
	}
	if in.PhotoComposition != nil {
		if err := applyPhotoComposition(file, in.PhotoComposition); err != nil {
			return "", err
		}
	} else if in.Background != nil {
		if err := applyBackgroundImage(file, *in.Background); err != nil {
			return "", err
		}
	}
	applied, _ := deepCopyDesign(file)
	title := strings.TrimSpace(in.Title)
	if title == "" {
		title = template.Title
	}
	return s.persist.CreateDesign(ctx, in.WorkspaceID, title, applied, &userID)
}

// applyBackgroundImage installs the caller-selected material as the immutable
// bottom layer of every page. Template text and decoration stay above it and
// remain editable, so a template contributes visual styling rather than
// replacing the selected photo with its own page color.
func applyBackgroundImage(file map[string]any, image InstantiateImage) error {
	if image.DataBase64 == "" || !strings.HasPrefix(image.ContentType, "image/") {
		return ErrBadRequest
	}
	assetID := registerInlineImageAsset(file, image)
	for pageIndex, pageRaw := range asArr(file["pages"]) {
		page := asObj(pageRaw)
		width, height := asNum(page["width"]), asNum(page["height"])
		if width <= 0 || height <= 0 {
			return ErrBadRequest
		}
		background := map[string]any{
			"id":        fmt.Sprintf("contentswarm-background-%d", pageIndex),
			"name":      "ContentSwarm 素材背景",
			"type":      "image",
			"transform": map[string]any{"x": 0.0, "y": 0.0, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0},
			"size":      map[string]any{"width": width, "height": height},
			"opacity":   1.0,
			"blendMode": "normal",
			"locked":    true,
			"fit":       "cover",
			"source":    map[string]any{"assetId": assetID, "naturalWidth": 0.0, "naturalHeight": 0.0},
			"data":      map[string]any{"background": true, "source": "contentswarm-material-library"},
		}
		page["children"] = append([]any{background}, asArr(page["children"])...)
		delete(page, "background")
	}
	return nil
}

// registerInlineImageAsset keeps automation-supplied pixels in the standard
// design asset table. The browser editor resolves image nodes through assetId,
// while render endpoints inline the same data URL when preparing an export.
func registerInlineImageAsset(file map[string]any, image InstantiateImage) string {
	url := fmt.Sprintf("data:%s;base64,%s", image.ContentType, image.DataBase64)
	assets := asArr(file["assets"])
	for _, raw := range assets {
		asset := asObj(raw)
		if asStr(asset["url"]) == url && asStr(asset["id"]) != "" {
			return asStr(asset["id"])
		}
	}
	sum := sha256.Sum256([]byte(url))
	assetID := fmt.Sprintf("contentswarm-inline-%x", sum[:12])
	file["assets"] = append(assets, map[string]any{
		"id": assetID, "kind": "image", "url": url, "mime": image.ContentType, "checksum": fmt.Sprintf("%x", sum[:]),
	})
	return assetID
}

func fillImageFields(file map[string]any, declarations []any, values map[string]InstantiateImage) error {
	fieldNodes := make(map[string]string, len(declarations))
	for _, raw := range declarations {
		field := asObj(raw)
		if asStr(field["kind"]) == "image" {
			fieldNodes[asStr(field["label"])] = asStr(field["nodeId"])
		}
	}
	remaining := make(map[string]InstantiateImage, len(values))
	for label, value := range values {
		if fieldNodes[label] == "" || value.DataBase64 == "" || !strings.HasPrefix(value.ContentType, "image/") {
			return ErrBadRequest
		}
		remaining[fieldNodes[label]] = value
	}
	for _, pageRaw := range asArr(file["pages"]) {
		for _, root := range asArr(asObj(pageRaw)["children"]) {
			visitTree(asObj(root), func(node map[string]any) {
				value, ok := remaining[asStr(node["id"])]
				if !ok {
					return
				}
				for key := range node {
					if key != "id" && key != "transform" && key != "size" && key != "opacity" && key != "blendMode" {
						delete(node, key)
					}
				}
				assetID := registerInlineImageAsset(file, value)
				node["type"] = "image"
				node["fit"] = "cover"
				node["source"] = map[string]any{"assetId": assetID, "naturalWidth": 0, "naturalHeight": 0}
				delete(remaining, asStr(node["id"]))
			})
		}
	}
	if len(remaining) > 0 {
		return ErrBadRequest
	}
	return nil
}

func fillTextFields(file map[string]any, declarations []any, values map[string]string) error {
	fieldNodes := make(map[string]string, len(declarations))
	for _, raw := range declarations {
		field := asObj(raw)
		if asStr(field["kind"]) == "text" {
			if asStr(field["semanticRole"]) == "label" {
				continue
			}
			label := asStr(field["label"])
			key := asStr(field["key"])
			if key == "" {
				key = label
			}
			fieldNodes[key] = asStr(field["nodeId"])
			fieldNodes[label] = asStr(field["nodeId"])
			constraints := asObj(field["constraints"])
			value, present := values[key]
			if !present {
				value, present = values[label]
			}
			if !present {
				continue
			}
			if required, _ := constraints["required"].(bool); required && strings.TrimSpace(value) == "" {
				return ErrBadRequest
			}
			if maxChars := int(asNum(constraints["maxChars"])); maxChars > 0 && present && utf8.RuneCountInString(strings.ReplaceAll(value, "\n", "")) > maxChars {
				return ErrBadRequest
			}
			if maxLines := int(asNum(constraints["maxLines"])); maxLines > 0 && present && strings.Count(value, "\n")+1 > maxLines {
				return ErrBadRequest
			}
			if maxCharsPerLine := int(asNum(constraints["maxCharsPerLine"])); maxCharsPerLine > 0 && present {
				for _, line := range strings.Split(value, "\n") {
					if utf8.RuneCountInString(line) > maxCharsPerLine {
						return ErrBadRequest
					}
				}
			}
		}
	}
	for label := range values {
		if fieldNodes[label] == "" {
			return ErrBadRequest
		}
	}

	remaining := make(map[string]string, len(values))
	for label, value := range values {
		remaining[fieldNodes[label]] = value
	}
	for _, pageRaw := range asArr(file["pages"]) {
		for _, root := range asArr(asObj(pageRaw)["children"]) {
			visitTree(asObj(root), func(node map[string]any) {
				value, ok := remaining[asStr(node["id"])]
				if !ok || asStr(node["type"]) != "text" {
					return
				}
				paragraphs := asArr(node["content"])
				if len(paragraphs) == 0 {
					return
				}
				first := asObj(paragraphs[0])
				runs := asArr(first["runs"])
				if len(runs) == 0 {
					return
				}
				for _, declarationRaw := range declarations {
					declaration := asObj(declarationRaw)
					if asStr(declaration["nodeId"]) == asStr(node["id"]) {
						restoreTemplateTypography(node, asObj(declaration["typography"]))
						break
					}
				}
				for paragraphIndex, paragraphRaw := range paragraphs {
					paragraphRuns := asArr(asObj(paragraphRaw)["runs"])
					for runIndex, runRaw := range paragraphRuns {
						text := ""
						if paragraphIndex == 0 && runIndex == 0 {
							text = value
						}
						asObj(runRaw)["text"] = text
					}
				}
				delete(remaining, asStr(node["id"]))
			})
		}
	}
	if len(remaining) > 0 {
		return ErrBadRequest
	}
	adjustTextFieldFlow(file, declarations)
	return nil
}

const (
	titleSubtitleGap    = 16.0
	titleFlowPageMargin = 32.0
)

// adjustTextFieldFlow keeps a vertically stacked subtitle below the rendered
// height of a wrapped title. Template coordinates remain authoritative for
// short text; only content that reaches the subtitle moves the subtitle. Fixed
// boxes grow when their text needs more room, and bottom-aligned title groups
// move upward so the subtitle remains inside the page. Both nodes are updated
// in the instantiated design, so the editor and server export share the same
// layout.
func adjustTextFieldFlow(file map[string]any, declarations []any) {
	titleID, subtitleID := "", ""
	for _, raw := range declarations {
		field := asObj(raw)
		if asStr(field["kind"]) != "text" {
			continue
		}
		switch asStr(field["semanticRole"]) {
		case "title":
			titleID = asStr(field["nodeId"])
		case "subtitle":
			subtitleID = asStr(field["nodeId"])
		}
	}
	if titleID == "" || subtitleID == "" {
		return
	}

	for _, pageRaw := range asArr(file["pages"]) {
		var title, subtitle map[string]any
		for _, childRaw := range asArr(asObj(pageRaw)["children"]) {
			child := asObj(childRaw)
			switch asStr(child["id"]) {
			case titleID:
				title = child
			case subtitleID:
				subtitle = child
			}
		}
		if asStr(title["type"]) != "text" || asStr(subtitle["type"]) != "text" {
			continue
		}
		titleTransform, subtitleTransform := asObj(title["transform"]), asObj(subtitle["transform"])
		if asNum(titleTransform["rotation"]) != 0 || asNum(subtitleTransform["rotation"]) != 0 {
			continue
		}
		titleX, titleY := asNum(titleTransform["x"]), asNum(titleTransform["y"])
		subtitleX, subtitleY := asNum(subtitleTransform["x"]), asNum(subtitleTransform["y"])
		if subtitleY <= titleY {
			continue
		}
		titleWidth := asNum(asObj(title["box"])["width"])
		if titleWidth <= 0 {
			titleWidth = asNum(asObj(title["size"])["width"])
		}
		subtitleWidth := asNum(asObj(subtitle["box"])["width"])
		if subtitleWidth <= 0 {
			subtitleWidth = asNum(asObj(subtitle["size"])["width"])
		}
		if titleX+titleWidth <= subtitleX || subtitleX+subtitleWidth <= titleX {
			continue
		}
		box := asObj(title["box"])
		if enabled, _ := asObj(box["autoFit"])["enabled"].(bool); enabled {
			continue
		}
		height := estimateAutoHeightText(title)
		if height <= 0 {
			continue
		}
		if current := asNum(box["height"]); height > current {
			box["height"] = height
			if size := asObj(title["size"]); size != nil {
				size["height"] = height
			}
		}
		desiredY := titleY + height + titleSubtitleGap
		if desiredY <= subtitleY {
			continue
		}

		subtitleHeight := estimateAutoHeightText(subtitle)
		if subtitleHeight <= 0 {
			subtitleHeight = asNum(asObj(subtitle["size"])["height"])
		}
		subtitleBox := asObj(subtitle["box"])
		if current := asNum(subtitleBox["height"]); subtitleHeight > current {
			subtitleBox["height"] = subtitleHeight
			if size := asObj(subtitle["size"]); size != nil {
				size["height"] = subtitleHeight
			}
		}

		if pageHeight := asNum(asObj(pageRaw)["height"]); pageHeight > 0 {
			overflow := desiredY + subtitleHeight + titleFlowPageMargin - pageHeight
			if overflow > 0 {
				maxShift := titleY - titleFlowPageMargin
				if maxShift < 0 {
					maxShift = 0
				}
				if overflow > maxShift {
					overflow = maxShift
				}
				titleY -= overflow
				desiredY -= overflow
				titleTransform["y"] = titleY
			}
		}
		subtitleTransform["y"] = desiredY
	}
}

type templateTextChunk struct {
	text       string
	whitespace bool
}

func templateWrapChunks(text string) []templateTextChunk {
	var chunks []templateTextChunk
	var builder strings.Builder
	whitespace, started := false, false
	flush := func() {
		if builder.Len() == 0 {
			return
		}
		chunks = append(chunks, templateTextChunk{text: builder.String(), whitespace: whitespace})
		builder.Reset()
		started = false
	}
	for _, r := range text {
		isWhitespace := unicode.IsSpace(r)
		isBreakable := unicode.In(r, unicode.Han, unicode.Hiragana, unicode.Katakana, unicode.Hangul) || unicode.IsPunct(r)
		if isBreakable {
			flush()
			chunks = append(chunks, templateTextChunk{text: string(r)})
			continue
		}
		if started && isWhitespace != whitespace {
			flush()
		}
		builder.WriteRune(r)
		whitespace = isWhitespace
		started = true
	}
	flush()
	return chunks
}

func estimateAutoHeightText(node map[string]any) float64 {
	box := asObj(node["box"])
	padding := asObj(box["padding"])
	contentWidth := asNum(box["width"]) - asNum(padding["l"]) - asNum(padding["r"])
	if contentWidth <= 0 {
		return 0
	}
	height := asNum(padding["t"]) + asNum(padding["b"])
	for paragraphIndex, paragraphRaw := range asArr(node["content"]) {
		paragraph := asObj(paragraphRaw)
		paragraphStyle := asObj(paragraph["style"])
		if paragraphIndex > 0 {
			height += asNum(paragraphStyle["spaceBefore"])
		}
		lineWidth, lineHeight := 0.0, 0.0
		flush := func() {
			if lineHeight == 0 {
				lineHeight = 16 * 1.2
			}
			height += lineHeight
			lineWidth, lineHeight = 0, 0
		}
		for _, runRaw := range asArr(paragraph["runs"]) {
			run := asObj(runRaw)
			style := asObj(run["style"])
			fontSize := asNum(style["fontSize"])
			if fontSize <= 0 {
				fontSize = 16
			}
			runHeight := estimatedLineHeight(style, fontSize)
			letterSpacing := asNum(style["letterSpacing"])
			for _, chunk := range templateWrapChunks(asStr(run["text"])) {
				chunkWidth := 0.0
				for _, r := range chunk.text {
					advance := fontSize
					if r == '\n' || r == '\r' {
						advance = 0
					} else if r <= unicode.MaxASCII {
						advance = fontSize * 0.55
					}
					chunkWidth += advance + letterSpacing
				}
				if lineWidth > 0 && lineWidth+chunkWidth > contentWidth && !chunk.whitespace {
					flush()
				}
				lineWidth += chunkWidth
				if runHeight > lineHeight {
					lineHeight = runHeight
				}
			}
		}
		flush()
		height += asNum(paragraphStyle["spaceAfter"])
	}
	return height
}

func estimatedLineHeight(style map[string]any, fontSize float64) float64 {
	if value := asNum(style["lineHeight"]); value > 0 {
		return fontSize * value
	}
	lineHeight := asObj(style["lineHeight"])
	switch asStr(lineHeight["mode"]) {
	case "absolute":
		if value := asNum(lineHeight["value"]); value > 0 {
			return value
		}
	case "multiple":
		if value := asNum(lineHeight["value"]); value > 0 {
			return fontSize * value
		}
	}
	return fontSize * 1.2
}

// restoreTemplateTypography makes the saved field contract authoritative at
// instantiation time. Older templates only contain the compact runs/alignment
// snapshot; newer templates also retain the complete rich-text, paragraph,
// text-box, and text-effect values used by the renderer.
func restoreTemplateTypography(node map[string]any, typography map[string]any) {
	if typography == nil {
		return
	}
	paragraphs := asArr(node["content"])
	contracts := asArr(typography["paragraphs"])
	if len(contracts) > 0 {
		for paragraphIndex, contractRaw := range contracts {
			if paragraphIndex >= len(paragraphs) {
				break
			}
			paragraph := asObj(paragraphs[paragraphIndex])
			contract := asObj(contractRaw)
			if style := asObj(contract["style"]); style != nil {
				paragraph["style"] = deepCloneValue(style)
			}
			runs := asArr(paragraph["runs"])
			for runIndex, runContractRaw := range asArr(contract["runs"]) {
				if runIndex >= len(runs) {
					break
				}
				if style := asObj(asObj(runContractRaw)["style"]); style != nil {
					asObj(runs[runIndex])["style"] = deepCloneValue(style)
				}
			}
		}
		if box := asObj(typography["box"]); box != nil {
			node["box"] = deepCloneValue(box)
		}
		if effects, ok := typography["textEffects"].([]any); ok {
			node["textEffects"] = deepCloneValue(effects)
		}
		return
	}

	// Backward compatibility for contracts saved before full style snapshots.
	if align := asStr(typography["paragraphAlign"]); align != "" && len(paragraphs) > 0 {
		style := asObj(asObj(paragraphs[0])["style"])
		if style == nil {
			style = map[string]any{}
			asObj(paragraphs[0])["style"] = style
		}
		style["align"] = align
	}
	runContracts := asArr(typography["runs"])
	contractIndex := 0
	for _, paragraphRaw := range paragraphs {
		for _, runRaw := range asArr(asObj(paragraphRaw)["runs"]) {
			if contractIndex >= len(runContracts) {
				return
			}
			contract := asObj(runContracts[contractIndex])
			style := asObj(asObj(runRaw)["style"])
			if style == nil {
				style = map[string]any{}
				asObj(runRaw)["style"] = style
			}
			for _, key := range []string{"fontFamily", "fontStyle", "fontSize", "letterSpacing", "lineHeight"} {
				if value, ok := contract[key]; ok {
					style[key] = deepCloneValue(value)
				}
			}
			if weight := asNum(contract["fontWeight"]); weight > 0 {
				axes := asObj(style["axes"])
				if axes == nil {
					axes = map[string]any{}
					style["axes"] = axes
				}
				axes["wght"] = weight
			}
			contractIndex++
		}
	}
}
