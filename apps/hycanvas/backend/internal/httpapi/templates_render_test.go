package httpapi

import (
	"bytes"
	"image/png"
	"net/http"
	"testing"

	"hycanvas/backend/internal/apikeys"
)

func TestRenderTemplatePreviewCreatesScaledPNG(t *testing.T) {
	file := map[string]any{
		"pages": []any{map[string]any{
			"width": 100.0, "height": 200.0,
			"children": []any{},
		}},
	}

	data, err := renderTemplatePreview(file, nil)
	if err != nil {
		t.Fatalf("renderTemplatePreview: %v", err)
	}
	image, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatalf("decode preview PNG: %v", err)
	}
	if image.Bounds().Dx() != 25 || image.Bounds().Dy() != 50 {
		t.Fatalf("preview dimensions = %dx%d, want 25x50", image.Bounds().Dx(), image.Bounds().Dy())
	}
}

func TestTemplatePreviewAPIKeyRouteRequiresExportScope(t *testing.T) {
	route, designID, ok := matchAPIKeyRoute(http.MethodGet, "/api/v1/templates/template-id/render.png")
	if !ok {
		t.Fatal("template preview route is not available to API keys")
	}
	if route.scope != apikeys.ScopeExport || designID != "" {
		t.Fatalf("template preview route = scope %q design %q", route.scope, designID)
	}
}

func TestTemplateBackgroundPreviewAPIKeyRouteRequiresExportScope(t *testing.T) {
	route, designID, ok := matchAPIKeyRoute(http.MethodPost, "/api/v1/templates/template-id/preview.png")
	if !ok {
		t.Fatal("template background preview route is not available to API keys")
	}
	if route.scope != apikeys.ScopeExport || designID != "" {
		t.Fatalf("template background preview route = scope %q design %q", route.scope, designID)
	}
}
