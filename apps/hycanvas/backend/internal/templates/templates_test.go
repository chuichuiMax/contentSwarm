package templates

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"

	"hycanvas/backend/internal/accounts"
	"hycanvas/backend/internal/persistence"
	"hycanvas/backend/internal/storage"
)

func stripSchema(dsn string) string {
	for _, sep := range []string{"?schema=", "&schema="} {
		if i := strings.Index(dsn, sep); i >= 0 {
			return dsn[:i]
		}
	}
	return dsn
}

func TestSeedLoads(t *testing.T) {
	// The built-in catalog is intentionally empty by default. If seed templates
	// are present, they must be well-formed and findable by id.
	if len(seedEntries) == 0 {
		t.Skip("no built-in seed templates")
	}
	first := seedEntries[0].toTemplate()
	if first.ID == "" || first.Title == "" {
		t.Fatalf("seed template missing id/title: %+v", first)
	}
	if _, ok := findSeed(first.ID); !ok {
		t.Fatalf("findSeed should locate %q", first.ID)
	}
}

func TestSearchTemplates(t *testing.T) {
	pool := []Template{
		{ID: "1", Title: "Birthday Poster", Tags: []string{"party"}, Categories: []string{"poster"}},
		{ID: "2", Title: "Resume", Tags: []string{"cv"}, Categories: []string{"doc"}},
		{ID: "3", Title: "Birthday Card", Tags: []string{"birthday"}, Categories: []string{"card"}},
	}
	res := searchTemplates(pool, TemplateQuery{Text: "birthday"})
	if len(res) != 2 {
		t.Fatalf("text search should match 2, got %d", len(res))
	}
	// Category filter.
	res2 := searchTemplates(pool, TemplateQuery{Categories: []string{"doc"}})
	if len(res2) != 1 || res2[0].ID != "2" {
		t.Fatalf("category filter wrong: %+v", res2)
	}
}

func TestRowToTemplateUsesDeclaredFieldsFromDesignMeta(t *testing.T) {
	file, err := json.Marshal(map[string]any{
		"pages": []any{map[string]any{"width": 1080, "height": 1440}},
		"meta": map[string]any{"brandEditableFields": []any{map[string]any{
			"nodeId": "project-name", "kind": "text", "label": "项目名称",
			"key": "project_name", "semanticRole": "project_name",
		}}},
	})
	if err != nil {
		t.Fatalf("marshal design: %v", err)
	}
	now := time.Now()
	template := rowToTemplate(TemplateRow{
		ID: "custom-template", Title: "项目案例封面", Visibility: "workspace",
		File: file, Style: json.RawMessage(`{}`), FillableFields: json.RawMessage(`[]`),
		Attributions: json.RawMessage(`[]`), CreatedAt: now, UpdatedAt: now,
	})

	if len(template.FillableFields) != 1 {
		t.Fatalf("fillable fields = %+v", template.FillableFields)
	}
	field := asObj(template.FillableFields[0])
	if asStr(field["semanticRole"]) != "project_name" || asStr(field["nodeId"]) != "project-name" {
		t.Fatalf("declared field = %+v", field)
	}
}

func TestDeepCopyDesign(t *testing.T) {
	file := map[string]any{
		"id": "orig",
		"pages": []any{map[string]any{
			"id": "p1", "children": []any{
				map[string]any{"id": "a", "type": "shape"},
				map[string]any{"id": "c", "type": "connector", "start": map[string]any{"attach": map[string]any{"nodeId": "a"}}},
			},
		}},
	}
	copy, idMap := deepCopyDesign(file)
	if copy["id"] == "orig" {
		t.Fatal("design id should be regenerated")
	}
	// Source not mutated.
	if file["id"] != "orig" {
		t.Fatal("source design must not be mutated")
	}
	page := copy["pages"].([]any)[0].(map[string]any)
	if page["id"] == "p1" {
		t.Fatal("page id should be regenerated")
	}
	kids := page["children"].([]any)
	newA := kids[0].(map[string]any)["id"].(string)
	if newA == "a" {
		t.Fatal("node id should be regenerated")
	}
	// Connector attach remapped to the new node id.
	conn := kids[1].(map[string]any)
	attach := conn["start"].(map[string]any)["attach"].(map[string]any)
	if attach["nodeId"] != newA {
		t.Fatalf("connector attach should remap to %q, got %v (idMap %v)", newA, attach["nodeId"], idMap["a"])
	}
}

func TestFillTextFieldsPreservesStyle(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{"children": []any{map[string]any{
			"id": "title-node", "type": "text",
			"content": []any{map[string]any{
				"runs": []any{
					map[string]any{"text": "old", "style": map[string]any{"fontSize": 72.0}},
					map[string]any{"text": " title", "style": map[string]any{"fontSize": 72.0}},
				},
				"style": map[string]any{"align": "center"},
			}},
		}}}},
	}
	fields := []any{map[string]any{"nodeId": "title-node", "kind": "text", "label": "主标题"}}
	if err := fillTextFields(file, fields, map[string]string{"主标题": "新标题"}); err != nil {
		t.Fatalf("fillTextFields: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	paragraph := asObj(asArr(node["content"])[0])
	runs := asArr(paragraph["runs"])
	if len(runs) != 2 || asStr(asObj(runs[0])["text"]) != "新标题" || asStr(asObj(runs[1])["text"]) != "" {
		t.Fatalf("filled runs = %+v", runs)
	}
	if asNum(asObj(asObj(runs[0])["style"])["fontSize"]) != 72 {
		t.Fatal("text style should be preserved")
	}
	if asStr(asObj(paragraph["style"])["align"]) != "center" {
		t.Fatal("paragraph style should be preserved")
	}
}

func TestFillTextFieldsRestoresCompleteTypographyContract(t *testing.T) {
	originalStyle := map[string]any{
		"fontFamily": "Noto Sans SC", "fontStyle": "ExtraBold Italic", "fontSize": 72.0,
		"axes":          map[string]any{"wght": 800.0, "wdth": 87.5, "slnt": -8.0},
		"fill":          map[string]any{"type": "solid", "color": map[string]any{"srgb": map[string]any{"r": 1.0, "g": 0.5, "b": 0.0, "a": 0.8}}},
		"letterSpacing": 2.5, "kerning": "optical",
		"lineHeight":    map[string]any{"mode": "absolute", "value": 88.0},
		"baselineShift": 3.0, "case": "smallcaps",
		"decoration": []any{"underline", "strikethrough"}, "script": "super",
		"language": "zh-CN", "features": map[string]any{"liga": 0.0}, "link": "https://example.com",
	}
	paragraphStyle := map[string]any{
		"align": "justify", "direction": "ltr", "indentStart": 12.0, "indentEnd": 4.0,
		"firstLineIndent": 8.0, "spaceBefore": 3.0, "spaceAfter": 6.0,
		"list": map[string]any{"type": "bullet", "level": 1.0, "marker": "•"}, "tabStops": []any{40.0, 80.0},
	}
	box := map[string]any{
		"mode": "fixed", "width": 320.0, "height": 120.0,
		"columns": map[string]any{"count": 2.0, "gutter": 16.0},
		"padding": map[string]any{"t": 5.0, "r": 6.0, "b": 7.0, "l": 8.0},
		"autoFit": map[string]any{"enabled": true, "min": 18.0, "max": 72.0}, "verticalAlign": "middle",
	}
	effects := []any{map[string]any{
		"kind": "shadow", "dx": 4.0, "dy": 5.0, "blur": 6.0, "opacity": 0.4,
		"color": map[string]any{"type": "solid", "color": map[string]any{"srgb": map[string]any{"r": 0.0, "g": 0.0, "b": 0.0, "a": 1.0}}},
	}}
	file := map[string]any{"pages": []any{map[string]any{"children": []any{map[string]any{
		"id": "title-node", "type": "text", "box": deepCloneValue(box), "textEffects": deepCloneValue(effects),
		"content": []any{map[string]any{
			"runs":  []any{map[string]any{"text": "旧标题", "style": deepCloneValue(originalStyle)}},
			"style": deepCloneValue(paragraphStyle),
		}},
	}}}}}
	field := map[string]any{"nodeId": "title-node", "kind": "text", "label": "主标题"}
	if err := normalizeTemplateTypography(file, []any{field}); err != nil {
		t.Fatalf("normalizeTemplateTypography: %v", err)
	}

	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	run := asObj(asArr(asObj(asArr(node["content"])[0])["runs"])[0])
	asObj(run["style"])["fontFamily"] = "system"
	asObj(run["style"])["axes"] = map[string]any{"wght": 400.0}
	asObj(run["style"])["decoration"] = []any{}
	asObj(asArr(node["content"])[0])["style"] = map[string]any{"align": "left", "direction": "auto"}
	node["box"] = map[string]any{"mode": "fixed", "width": 320.0, "height": 120.0}
	node["textEffects"] = []any{}

	if err := fillTextFields(file, []any{field}, map[string]string{"主标题": "替换标题"}); err != nil {
		t.Fatalf("fillTextFields: %v", err)
	}
	if asStr(run["text"]) != "替换标题" {
		t.Fatalf("text = %q", asStr(run["text"]))
	}
	if !reflect.DeepEqual(asObj(run["style"]), originalStyle) {
		t.Fatalf("run style was not restored\ngot:  %#v\nwant: %#v", asObj(run["style"]), originalStyle)
	}
	if !reflect.DeepEqual(asObj(asObj(asArr(node["content"])[0])["style"]), paragraphStyle) {
		t.Fatalf("paragraph style was not restored: %#v", asObj(asObj(asArr(node["content"])[0])["style"]))
	}
	if !reflect.DeepEqual(asObj(node["box"]), box) || !reflect.DeepEqual(asArr(node["textEffects"]), effects) {
		t.Fatalf("text box or effects were not restored: box=%#v effects=%#v", node["box"], node["textEffects"])
	}
}

func TestFillTextFieldsRestoresLegacyTypographyContract(t *testing.T) {
	file := map[string]any{"pages": []any{map[string]any{"children": []any{map[string]any{
		"id": "title-node", "type": "text",
		"content": []any{map[string]any{
			"runs":  []any{map[string]any{"text": "旧标题", "style": map[string]any{"fontFamily": "system", "fontStyle": "Regular", "fontSize": 20.0}}},
			"style": map[string]any{"align": "left", "direction": "auto"},
		}},
	}}}}}
	field := map[string]any{
		"nodeId": "title-node", "kind": "text", "label": "主标题",
		"typography": map[string]any{
			"paragraphAlign": "center",
			"runs": []any{map[string]any{
				"fontFamily": "Noto Sans SC", "fontStyle": "ExtraBold", "fontWeight": 800.0,
				"fontSize": 64.0, "letterSpacing": 2.0,
				"lineHeight": map[string]any{"mode": "multiple", "value": 1.1},
			}},
		},
	}
	if err := fillTextFields(file, []any{field}, map[string]string{"主标题": "新标题"}); err != nil {
		t.Fatalf("fillTextFields: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	paragraph := asObj(asArr(node["content"])[0])
	style := asObj(asObj(asArr(paragraph["runs"])[0])["style"])
	if asStr(style["fontFamily"]) != "Noto Sans SC" || asNum(asObj(style["axes"])["wght"]) != 800 || asNum(style["fontSize"]) != 64 {
		t.Fatalf("legacy typography was not restored: %#v", style)
	}
	if asStr(asObj(paragraph["style"])["align"]) != "center" {
		t.Fatalf("legacy paragraph alignment was not restored: %#v", paragraph["style"])
	}
}

func TestFillTextFieldsUsesUniqueKeysWhenLabelsRepeat(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{"children": []any{
			map[string]any{"id": "title-a", "type": "text", "content": []any{map[string]any{"runs": []any{map[string]any{"text": "old"}}}}},
			map[string]any{"id": "title-b", "type": "text", "content": []any{map[string]any{"runs": []any{map[string]any{"text": "old"}}}}},
		}}},
	}
	fields := []any{
		map[string]any{"nodeId": "title-a", "kind": "text", "key": "field_1", "label": "重复原文"},
		map[string]any{"nodeId": "title-b", "kind": "text", "key": "field_2", "label": "重复原文"},
	}
	if err := fillTextFields(file, fields, map[string]string{"field_1": "空间焕新", "field_2": "复尺规划"}); err != nil {
		t.Fatalf("fillTextFields: %v", err)
	}
	children := asArr(asObj(asArr(file["pages"])[0])["children"])
	first := asStr(asObj(asArr(asObj(children[0])["content"])[0])["runs"].([]any)[0].(map[string]any)["text"])
	second := asStr(asObj(asArr(asObj(children[1])["content"])[0])["runs"].([]any)[0].(map[string]any)["text"])
	if first != "空间焕新" || second != "复尺规划" {
		t.Fatalf("filled texts = %q, %q", first, second)
	}
}

func TestNormalizeTemplateTypographyCanonicalizesSystemFontAndRecordsContract(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{"children": []any{map[string]any{
			"id": "title-node", "type": "text",
			"content": []any{map[string]any{
				"runs": []any{map[string]any{"text": "主标题", "style": map[string]any{
					"fontFamily": "system", "fontStyle": "Bold", "fontSize": 72.0,
					"axes": map[string]any{"wght": 650.0}, "letterSpacing": 1.5,
				}}},
				"style": map[string]any{"align": "center"},
			}},
		}}}},
	}
	field := map[string]any{"nodeId": "title-node", "kind": "text", "label": "主标题"}
	if err := normalizeTemplateTypography(file, []any{field}); err != nil {
		t.Fatalf("normalizeTemplateTypography: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	run := asObj(asArr(asObj(asArr(node["content"])[0])["runs"])[0])
	if got := asStr(asObj(run["style"])["fontFamily"]); got != canonicalSystemFont {
		t.Fatalf("fontFamily = %q, want %q", got, canonicalSystemFont)
	}
	typography := asObj(field["typography"])
	if asStr(typography["paragraphAlign"]) != "center" {
		t.Fatalf("paragraphAlign = %q", asStr(typography["paragraphAlign"]))
	}
	snapshot := asObj(asArr(typography["runs"])[0])
	if asStr(snapshot["fontFamily"]) != canonicalSystemFont || int(asNum(snapshot["fontWeight"])) != 650 || asNum(snapshot["fontSize"]) != 72 {
		t.Fatalf("typography snapshot = %+v", snapshot)
	}
}

func TestNormalizeTemplateTypographyRejectsMissingTextNode(t *testing.T) {
	file := map[string]any{"pages": []any{}}
	fields := []any{map[string]any{"nodeId": "missing", "kind": "text", "label": "主标题"}}
	if err := normalizeTemplateTypography(file, fields); err != ErrBadRequest {
		t.Fatalf("expected ErrBadRequest, got %v", err)
	}
}

func TestFillTextFieldsDoesNotChangeTemplateStyleOrStructure(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{"children": []any{map[string]any{
			"id": "title-node", "type": "text",
			"box": map[string]any{
				"mode": "fixed", "width": 300.0, "height": 80.0,
				"autoFit": map[string]any{"enabled": false, "min": 10.0, "max": 140.0},
			},
			"content": []any{
				map[string]any{"runs": []any{map[string]any{"text": "短", "style": map[string]any{"fontSize": 140.0}}}},
				map[string]any{"runs": []any{map[string]any{"text": "标题", "style": map[string]any{"fontSize": 80.0}}}},
			},
		}}}},
	}
	fields := []any{map[string]any{"nodeId": "title-node", "kind": "text", "label": "主标题"}}
	if err := fillTextFields(file, fields, map[string]string{"主标题": "替换后更长的封面标题"}); err != nil {
		t.Fatalf("fillTextFields: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	box := asObj(node["box"])
	autoFit := asObj(box["autoFit"])
	if enabled, _ := autoFit["enabled"].(bool); enabled {
		t.Fatal("text replacement must preserve the template auto-fit setting")
	}
	if asNum(autoFit["min"]) != 10 || asNum(autoFit["max"]) != 140 {
		t.Fatalf("auto-fit bounds should be preserved: %+v", autoFit)
	}
	paragraphs := asArr(node["content"])
	if len(paragraphs) != 2 {
		t.Fatalf("template paragraphs changed: %+v", paragraphs)
	}
	firstRun := asObj(asArr(asObj(paragraphs[0])["runs"])[0])
	secondRun := asObj(asArr(asObj(paragraphs[1])["runs"])[0])
	if asStr(firstRun["text"]) != "替换后更长的封面标题" || asStr(secondRun["text"]) != "" {
		t.Fatalf("only text values should change: %+v", paragraphs)
	}
	if asNum(asObj(firstRun["style"])["fontSize"]) != 140 || asNum(asObj(secondRun["style"])["fontSize"]) != 80 {
		t.Fatalf("template font sizes changed: %+v", paragraphs)
	}
}

func TestFillTextFieldsRejectsUnknownLabel(t *testing.T) {
	file := map[string]any{"pages": []any{}}
	if err := fillTextFields(file, nil, map[string]string{"不存在": "value"}); err != ErrBadRequest {
		t.Fatalf("expected ErrBadRequest, got %v", err)
	}
}

func TestFillTextFieldsEnforcesRequiredAndMaxChars(t *testing.T) {
	file := map[string]any{"pages": []any{}}
	fields := []any{map[string]any{
		"nodeId": "title-node", "kind": "text", "label": "项目名称",
		"constraints": map[string]any{"required": true, "maxChars": 4.0},
	}}
	if err := fillTextFields(file, fields, map[string]string{}); err != ErrBadRequest {
		t.Fatalf("missing required field: expected ErrBadRequest, got %v", err)
	}
	if err := fillTextFields(file, fields, map[string]string{"项目名称": "岳阳杏林小区"}); err != ErrBadRequest {
		t.Fatalf("oversized field: expected ErrBadRequest, got %v", err)
	}
}

func TestFillTextFieldsAcceptsDeclaredMultilineText(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{
			"children": []any{map[string]any{
				"id": "title-node", "type": "text",
				"content": []any{map[string]any{"runs": []any{map[string]any{"text": "旧标题"}}}},
			}},
		}},
	}
	fields := []any{map[string]any{
		"nodeId": "title-node", "kind": "text", "label": "主标题",
		"constraints": map[string]any{"maxChars": 13.0, "maxCharsPerLine": 7.0, "maxLines": 2.0},
	}}
	if err := fillTextFields(file, fields, map[string]string{"主标题": "真香，89㎡收\n纳远超预期"}); err != nil {
		t.Fatalf("declared multiline text should be accepted: %v", err)
	}
}

func TestFillTextFieldsPreservesLabelText(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{"children": []any{map[string]any{
			"id": "label-node", "type": "text",
			"content": []any{map[string]any{"runs": []any{map[string]any{"text": "VILLA INTERIOR DESIGN"}}}},
		}}}},
	}
	fields := []any{map[string]any{
		"nodeId": "label-node", "kind": "text", "label": "英文装饰标签", "semanticRole": "label",
		"constraints": map[string]any{"required": true, "maxChars": 4.0},
	}}
	if err := fillTextFields(file, fields, map[string]string{}); err != nil {
		t.Fatalf("label field should not require a replacement: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	run := asObj(asArr(asObj(asArr(node["content"])[0])["runs"])[0])
	if asStr(run["text"]) != "VILLA INTERIOR DESIGN" {
		t.Fatalf("label text was replaced: %+v", run)
	}
}

func TestFillImageFieldsReplacesDeclaredNode(t *testing.T) {
	file := map[string]any{
		"assets": []any{},
		"pages": []any{map[string]any{"children": []any{map[string]any{
			"id": "image-node", "type": "text",
			"transform": map[string]any{"x": 10.0, "y": 20.0},
			"size":      map[string]any{"width": 300.0, "height": 200.0},
			"content":   []any{},
		}}}},
	}
	fields := []any{map[string]any{"nodeId": "image-node", "kind": "image", "label": "主图"}}
	err := fillImageFields(file, fields, map[string]InstantiateImage{
		"主图": {ContentType: "image/png", DataBase64: "cG5n"},
	})
	if err != nil {
		t.Fatalf("fillImageFields: %v", err)
	}
	node := asObj(asArr(asObj(asArr(file["pages"])[0])["children"])[0])
	if asStr(node["type"]) != "image" {
		t.Fatalf("image node = %+v", node)
	}
	assetID := asStr(asObj(node["source"])["assetId"])
	assets := asArr(file["assets"])
	if assetID == "" || len(assets) != 1 || asStr(asObj(assets[0])["id"]) != assetID || asStr(asObj(assets[0])["url"]) != "data:image/png;base64,cG5n" {
		t.Fatalf("image asset reference missing: node=%+v assets=%+v", node, assets)
	}
	if _, ok := node["src"]; ok {
		t.Fatal("image pixels must live only in file.assets")
	}
	if asNum(asObj(node["transform"])["x"]) != 10 || asNum(asObj(node["size"])["width"]) != 300 {
		t.Fatal("image replacement must preserve geometry")
	}
}

func TestFillImageFieldsRejectsUndeclaredLabel(t *testing.T) {
	file := map[string]any{"pages": []any{}}
	err := fillImageFields(file, nil, map[string]InstantiateImage{
		"主图": {ContentType: "image/png", DataBase64: "cG5n"},
	})
	if err != ErrBadRequest {
		t.Fatalf("expected ErrBadRequest, got %v", err)
	}
}

func TestApplyBackgroundImageKeepsTemplateLayersAboveSelectedMaterial(t *testing.T) {
	file := map[string]any{
		"assets": []any{},
		"pages": []any{map[string]any{
			"width": 1080.0, "height": 1440.0,
			"background": map[string]any{"type": "solid"},
			"children":   []any{map[string]any{"id": "title", "type": "text"}},
		}},
	}
	err := applyBackgroundImage(file, InstantiateImage{ContentType: "image/png", DataBase64: "cG5n"})
	if err != nil {
		t.Fatalf("applyBackgroundImage: %v", err)
	}
	page := asObj(asArr(file["pages"])[0])
	if _, ok := page["background"]; ok {
		t.Fatal("template page color must not cover the selected material")
	}
	children := asArr(page["children"])
	if len(children) != 2 || asStr(asObj(children[0])["type"]) != "image" || asStr(asObj(children[1])["id"]) != "title" {
		t.Fatalf("layers = %+v", children)
	}
	background := asObj(children[0])
	if !background["locked"].(bool) {
		t.Fatalf("background = %+v", background)
	}
	assetID := asStr(asObj(background["source"])["assetId"])
	assets := asArr(file["assets"])
	if assetID == "" || len(assets) != 1 || asStr(asObj(assets[0])["id"]) != assetID || asStr(asObj(assets[0])["url"]) != "data:image/png;base64,cG5n" {
		t.Fatalf("background asset reference missing: background=%+v assets=%+v", background, assets)
	}
	if _, ok := background["src"]; ok {
		t.Fatal("background pixels must live only in file.assets")
	}
}

type tPersist struct{ p *persistence.Service }

func (a tPersist) CreateDesign(ctx context.Context, ws, title string, from map[string]any, author *string) (string, error) {
	rec, err := a.p.Create(ctx, ws, title, persistence.DesignFile(from), author)
	if err != nil {
		return "", err
	}
	return rec.ID, nil
}
func (a tPersist) GetWorkspaceID(ctx context.Context, id string) (string, error) {
	return a.p.GetWorkspaceID(ctx, id)
}
func (a tPersist) GetTemplateZone(ctx context.Context, id string) (string, error) {
	rec, err := a.p.GetRecord(ctx, id)
	if err != nil || rec.TemplateZone == nil {
		return "", err
	}
	return *rec.TemplateZone, nil
}
func (a tPersist) LoadDesignFile(ctx context.Context, id, ws string) (map[string]any, error) {
	l, err := a.p.LoadFile(ctx, id, ws)
	if err != nil {
		return nil, err
	}
	return l.File, nil
}

func TestTemplates_DB(t *testing.T) {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		t.Skip("DATABASE_URL not set; skipping DB integration test")
	}
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, stripSchema(dsn))
	if err != nil {
		t.Fatalf("connect: %v", err)
	}
	defer conn.Close(ctx)
	tx, err := conn.Begin(ctx)
	if err != nil {
		t.Fatalf("begin: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	store, _ := storage.NewLocal(t.TempDir())
	acct := accounts.NewService(tx, "test-jwt-secret")
	owner, ws, _, err := acct.Signup(ctx, "tpl-owner+"+uuid.NewString()+"@example.com", "a-strong-password", "Owner")
	if err != nil {
		t.Fatalf("signup: %v", err)
	}
	persist := persistence.NewService(tx).WithStorage(store)
	svc := NewService(tx, acct, tPersist{persist})

	// List includes the embedded seed catalog.
	list, err := svc.List(ctx, owner.ID, TemplateQuery{}, "", "")
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(list) < len(seedEntries) {
		t.Fatalf("list should include seed templates: %d < %d", len(list), len(seedEntries))
	}

	// Obtain a design to save as a template. Prefer applying a seed template
	// (which also exercises Apply); fall back to a directly-created design when
	// the built-in catalog is empty (the default).
	var designID string
	if len(seedEntries) > 0 {
		seedID := seedEntries[0].toTemplate().ID
		designID, err = svc.Apply(ctx, owner.ID, seedID, ws.ID)
		if err != nil {
			t.Fatalf("Apply seed: %v", err)
		}
	} else {
		design := map[string]any{
			"id": uuid.NewString(), "schemaVersion": 10, "title": "Source",
			"format": map[string]any{"width": 100, "height": 100, "unit": "px"},
			"unit":   "px", "dpi": 96,
			"pages":  []any{map[string]any{"id": "p1", "name": "Page 1", "width": 100, "height": 100, "children": []any{}}},
			"assets": []any{}, "fonts": []any{}, "meta": map[string]any{},
		}
		rec, cerr := persist.Create(ctx, ws.ID, "Source", persistence.DesignFile(design), nil)
		if cerr != nil {
			t.Fatalf("create design: %v", cerr)
		}
		designID = rec.ID
	}
	if _, err := persist.LoadFile(ctx, designID, ws.ID); err != nil {
		t.Fatalf("design should load: %v", err)
	}

	// Historical Xiaohongshu drafts may have no zone marker in the snapshot;
	// saving by design id must inherit the persisted design summary instead.
	zoneDesign := map[string]any{
		"id": uuid.NewString(), "schemaVersion": 24, "title": "小红书模板专区",
		"unit": "px", "dpi": 96,
		"pages":  []any{map[string]any{"id": "p-zone", "name": "Page 1", "width": 1080, "height": 1440, "children": []any{}}},
		"assets": []any{}, "fonts": []any{}, "meta": map[string]any{"templateZone": "xiaohongshu"},
	}
	zoneRec, err := persist.Create(ctx, ws.ID, "小红书模板专区", persistence.DesignFile(zoneDesign), &owner.ID)
	if err != nil {
		t.Fatalf("create zone design: %v", err)
	}
	zoneSaved, err := svc.SaveAsTemplate(ctx, owner.ID, SaveInput{WorkspaceID: ws.ID, DesignID: zoneRec.ID, Title: "Zone Template", Visibility: "workspace"})
	if err != nil || !contains(zoneSaved.Tags, "小红书") || len(zoneSaved.Categories) != 1 || zoneSaved.Categories[0] != "小红书" {
		t.Fatalf("zone tag not inherited: %+v err=%v", zoneSaved, err)
	}
	appliedZoneID, err := svc.Apply(ctx, owner.ID, zoneSaved.ID, ws.ID)
	if err != nil {
		t.Fatalf("apply zone template: %v", err)
	}
	appliedZone, err := persist.GetRecord(ctx, appliedZoneID)
	if err != nil || appliedZone.TemplateZone == nil || *appliedZone.TemplateZone != "xiaohongshu" {
		t.Fatalf("applied design should remain in Xiaohongshu zone: %+v err=%v", appliedZone, err)
	}

	// Save the design as a private template; it then appears in the list.
	loaded, _ := persist.LoadFile(ctx, designID, ws.ID)
	saved, err := svc.SaveAsTemplate(ctx, owner.ID, SaveInput{WorkspaceID: ws.ID, File: loaded.File, Title: "My Template", Visibility: "private"})
	if err != nil {
		t.Fatalf("SaveAsTemplate: %v", err)
	}
	if saved.Visibility != "personal" {
		t.Fatalf("private template should map to personal visibility: %s", saved.Visibility)
	}
	instantiatedID, err := svc.Instantiate(ctx, owner.ID, saved.ID, InstantiateInput{
		WorkspaceID: ws.ID,
		Title:       "Custom template cover",
		Fields:      map[string]string{},
		Background:  &InstantiateImage{ContentType: "image/png", DataBase64: "cG5n"},
	})
	if err != nil {
		t.Fatalf("instantiate custom template without fillable fields: %v", err)
	}
	instantiated, err := persist.LoadFile(ctx, instantiatedID, ws.ID)
	if err != nil {
		t.Fatalf("load instantiated custom template: %v", err)
	}
	instantiatedPage := asObj(asArr(instantiated.File["pages"])[0])
	instantiatedChildren := asArr(instantiatedPage["children"])
	if len(instantiatedChildren) == 0 {
		t.Fatalf("selected material background missing: %+v", instantiatedChildren)
	}
	backgroundAssetID := asStr(asObj(asObj(instantiatedChildren[0])["source"])["assetId"])
	instantiatedAssets := asArr(instantiated.File["assets"])
	if backgroundAssetID == "" || len(instantiatedAssets) == 0 || asStr(asObj(instantiatedAssets[len(instantiatedAssets)-1])["id"]) != backgroundAssetID || asStr(asObj(instantiatedAssets[len(instantiatedAssets)-1])["url"]) != "data:image/png;base64,cG5n" {
		t.Fatalf("selected material asset missing: background=%+v assets=%+v", instantiatedChildren[0], instantiatedAssets)
	}
	got, err := svc.Get(ctx, owner.ID, saved.ID)
	if err != nil || got.ID != saved.ID {
		t.Fatalf("Get saved: %+v err=%v", got, err)
	}
	var templateCountBefore int
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM "templates"`).Scan(&templateCountBefore); err != nil {
		t.Fatalf("count templates before rename: %v", err)
	}
	renamed, err := svc.Rename(ctx, owner.ID, saved.ID, "  Renamed Template  ")
	if err != nil || renamed.ID != saved.ID || renamed.Title != "Renamed Template" {
		t.Fatalf("rename should update the same template: %+v err=%v", renamed, err)
	}
	var templateCountAfter int
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM "templates"`).Scan(&templateCountAfter); err != nil {
		t.Fatalf("count templates after rename: %v", err)
	}
	if templateCountAfter != templateCountBefore {
		t.Fatalf("rename created a template: before=%d after=%d", templateCountBefore, templateCountAfter)
	}
	if _, err := svc.Rename(ctx, owner.ID, saved.ID, "  "); err != ErrBadRequest {
		t.Fatalf("blank template title should be rejected, got %v", err)
	}
	// A different user cannot see the private template.
	other, _, _, _ := acct.Signup(ctx, "tpl-other+"+uuid.NewString()+"@example.com", "a-strong-password", "Other")
	if _, err := svc.Get(ctx, other.ID, saved.ID); err != ErrNotFound {
		t.Fatalf("private template should be hidden from others, got %v", err)
	}
	if _, err := svc.Rename(ctx, other.ID, saved.ID, "Unauthorized"); err != ErrForbidden {
		t.Fatalf("non-owner should not rename a private template, got %v", err)
	}
	if err := svc.Delete(ctx, other.ID, saved.ID); err != ErrForbidden {
		t.Fatalf("non-owner should not delete a private template, got %v", err)
	}
	if err := svc.Delete(ctx, owner.ID, saved.ID); err != nil {
		t.Fatalf("owner should delete a private template: %v", err)
	}
	if _, err := svc.Get(ctx, owner.ID, saved.ID); err != ErrNotFound {
		t.Fatalf("deleted template should no longer exist, got %v", err)
	}
	if len(seedEntries) > 0 {
		if err := svc.Delete(ctx, owner.ID, seedEntries[0].toTemplate().ID); err != ErrForbidden {
			t.Fatalf("built-in template should not be deletable, got %v", err)
		}
	}

	// Collections: create, assign, list, delete.
	col, err := svc.CreateCollection(ctx, owner.ID, ws.ID, "Brand")
	if err != nil {
		t.Fatalf("CreateCollection: %v", err)
	}
	// Re-save as a workspace template so it can be collected (private is owner-only but workspace-scoped column is set).
	wsTmpl, err := svc.SaveAsTemplate(ctx, owner.ID, SaveInput{WorkspaceID: ws.ID, File: loaded.File, Title: "WS Tmpl", Visibility: "workspace"})
	if err != nil {
		t.Fatalf("save workspace template: %v", err)
	}
	workspaceTemplates, err := svc.List(ctx, owner.ID, TemplateQuery{}, ws.ID, "")
	if err != nil {
		t.Fatalf("list workspace templates: %v", err)
	}
	var listedWorkspaceTemplate *Template
	for i := range workspaceTemplates {
		if workspaceTemplates[i].ID == wsTmpl.ID {
			listedWorkspaceTemplate = &workspaceTemplates[i]
			break
		}
	}
	if listedWorkspaceTemplate == nil || listedWorkspaceTemplate.Visibility != "team" || listedWorkspaceTemplate.WorkspaceID == nil || *listedWorkspaceTemplate.WorkspaceID != ws.ID {
		t.Fatalf("workspace template scope metadata missing from list: %+v", listedWorkspaceTemplate)
	}
	if _, err := svc.AssignCollection(ctx, owner.ID, wsTmpl.ID, col.ID); err != nil {
		t.Fatalf("AssignCollection: %v", err)
	}
	inCol, err := svc.List(ctx, owner.ID, TemplateQuery{}, ws.ID, col.ID)
	if err != nil || len(inCol) != 1 || inCol[0].ID != wsTmpl.ID {
		t.Fatalf("collection filter wrong: %+v err=%v", inCol, err)
	}
	catalog, err := svc.PublicCategorizedCatalog(ctx)
	if err != nil {
		t.Fatalf("PublicCategorizedCatalog: %v", err)
	}
	var catalogCategory *Category
	for i := range catalog {
		if catalog[i].ID == col.ID {
			catalogCategory = &catalog[i]
			break
		}
	}
	if catalogCategory == nil || catalogCategory.Name != col.Name || len(catalogCategory.Templates) != 1 || catalogCategory.Templates[0].ID != wsTmpl.ID {
		t.Fatalf("categorized catalog wrong: %+v", catalog)
	}
	publicTmpl, err := svc.SaveAsTemplate(ctx, owner.ID, SaveInput{
		WorkspaceID: ws.ID, File: loaded.File, Title: "Public Tmpl", Visibility: "public", CollectionID: col.ID,
	})
	if err != nil {
		t.Fatalf("save public template in collection: %v", err)
	}
	if publicTmpl.Visibility != "public" || publicTmpl.WorkspaceID == nil || *publicTmpl.WorkspaceID != ws.ID {
		t.Fatalf("public template lost its collection workspace: %+v", publicTmpl)
	}
	inCol, err = svc.List(ctx, owner.ID, TemplateQuery{}, ws.ID, col.ID)
	if err != nil || len(inCol) != 2 {
		t.Fatalf("public template missing from workspace collection: %+v err=%v", inCol, err)
	}
	// Older public templates cleared workspace_id. Their collection still owns
	// the workspace, so they must remain visible in the selected category.
	if _, err := tx.Exec(ctx, `UPDATE "templates" SET "workspace_id" = NULL WHERE id = $1`, publicTmpl.ID); err != nil {
		t.Fatalf("simulate historical public template: %v", err)
	}
	inCol, err = svc.List(ctx, owner.ID, TemplateQuery{}, ws.ID, col.ID)
	if err != nil || len(inCol) != 2 {
		t.Fatalf("historical public template missing from workspace collection: %+v err=%v", inCol, err)
	}
	if err := svc.DeleteCollection(ctx, owner.ID, col.ID); err != nil {
		t.Fatalf("DeleteCollection: %v", err)
	}
	if err := svc.Delete(ctx, other.ID, wsTmpl.ID); err != ErrForbidden {
		t.Fatalf("non-member should not delete a workspace template, got %v", err)
	}
	if err := svc.Delete(ctx, owner.ID, wsTmpl.ID); err != nil {
		t.Fatalf("workspace member should delete a workspace template: %v", err)
	}
}

func TestPhotoCompositionPreservesTemplateAndEditableCells(t *testing.T) {
	var buffer bytes.Buffer
	img := image.NewRGBA(image.Rect(0, 0, 20, 10))
	img.Set(0, 0, color.RGBA{R: 255, A: 255})
	_ = png.Encode(&buffer, img)
	photo := CompositionImage{InstantiateImage: InstantiateImage{Filename: "test.png", ContentType: "image/png", DataBase64: base64.StdEncoding.EncodeToString(buffer.Bytes())}, FocalX: 0.2, FocalY: 0.8}
	in := &PhotoComposition{Rows: 1, Cols: 2, Gap: 8, Cells: []PhotoCell{{Row: 0, Col: 0, RowSpan: 1, ColSpan: 1}, {Row: 0, Col: 1, RowSpan: 1, ColSpan: 1}}, Images: []CompositionImage{photo, photo}}
	text := map[string]any{"id": "existing-title", "type": "text", "name": "原始字体与位置"}
	page := map[string]any{"width": 1080.0, "height": 1440.0, "children": []any{text}}
	file := map[string]any{"pages": []any{page}}
	if err := applyPhotoComposition(file, in); err != nil {
		t.Fatal(err)
	}
	roots := asArr(page["children"])
	if len(roots) != 2 || asStr(asObj(roots[1])["id"]) != "existing-title" {
		t.Fatal("template layers changed")
	}
	grid := asObj(roots[0])
	if grid["locked"] != false || grid["type"] != "grid" {
		t.Fatal("grid must remain editable")
	}
	children := asArr(grid["children"])
	if len(children) != 2 {
		t.Fatal("missing cells")
	}
	frame := asObj(children[1])
	if asNum(asObj(frame["transform"])["x"]) != 544 {
		t.Fatal("wrong layout")
	}
	imageNode := asObj(asArr(frame["children"])[0])
	if asNum(asObj(imageNode["focalPoint"])["x"]) != 0.2 {
		t.Fatal("crop lost")
	}
	if asNum(asObj(imageNode["source"])["naturalWidth"]) != 20 {
		t.Fatal("source dimensions missing")
	}
	in.Cells[1].Col = 0
	if err := applyPhotoComposition(file, in); err == nil {
		t.Fatal("overlapping cells accepted")
	}
}
