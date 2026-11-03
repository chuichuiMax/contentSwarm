package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hycanvas/backend/internal/apikeys"
	"hycanvas/backend/internal/templates"
)

type publicCatalogDB struct{}

func (publicCatalogDB) QueryRow(_ context.Context, sql string, args ...any) pgx.Row {
	return publicCatalogRow{found: bytes.Contains([]byte(sql), []byte("template_collections")) && len(args) == 1 && args[0] == "category-1"}
}
func (publicCatalogDB) Exec(context.Context, string, ...any) (pgconn.CommandTag, error) {
	return pgconn.CommandTag{}, nil
}
func (publicCatalogDB) Query(_ context.Context, sql string, _ ...any) (pgx.Rows, error) {
	return &publicCatalogRows{collectionQuery: bytes.Contains([]byte(sql), []byte("template_collections"))}, nil
}

type publicCatalogRows struct {
	collectionQuery bool
	read            bool
}

type publicCatalogRow struct {
	found bool
}

func (r publicCatalogRow) Scan(dest ...any) error {
	if !r.found {
		return pgx.ErrNoRows
	}
	*(dest[0].(*string)) = "category-1"
	*(dest[1].(*string)) = "workspace-1"
	*(dest[2].(*string)) = "内容报价"
	return nil
}

func (r *publicCatalogRows) Close()                                       {}
func (r *publicCatalogRows) Err() error                                   { return nil }
func (r *publicCatalogRows) CommandTag() pgconn.CommandTag                { return pgconn.CommandTag{} }
func (r *publicCatalogRows) FieldDescriptions() []pgconn.FieldDescription { return nil }
func (r *publicCatalogRows) RawValues() [][]byte                          { return nil }
func (r *publicCatalogRows) Conn() *pgx.Conn                              { return nil }
func (r *publicCatalogRows) NextResultSet() bool                          { return false }
func (r *publicCatalogRows) Values() ([]any, error)                       { return nil, nil }
func (r *publicCatalogRows) Next() bool {
	if r.read {
		return false
	}
	r.read = true
	return true
}
func (r *publicCatalogRows) Scan(dest ...any) error {
	if r.collectionQuery {
		*(dest[0].(*string)) = "category-1"
		*(dest[1].(*string)) = "workspace-1"
		*(dest[2].(*string)) = "内容报价"
		return nil
	}
	workspaceID := "workspace-1"
	category := "报价"
	collectionID := "category-1"
	now := time.Date(2026, time.September, 23, 0, 0, 0, 0, time.UTC)
	*(dest[0].(*string)) = "template-1"
	*(dest[1].(*string)) = "owner-1"
	*(dest[2].(**string)) = nil
	*(dest[3].(**string)) = &workspaceID
	*(dest[4].(*string)) = "手写报价"
	*(dest[5].(**string)) = &category
	*(dest[6].(*[]string)) = []string{"报价"}
	*(dest[7].(*json.RawMessage)) = json.RawMessage(`{"pages":[{"width":1080,"height":1440,"children":[]}]}`)
	*(dest[8].(**string)) = nil
	*(dest[9].(*string)) = "PUBLIC"
	*(dest[10].(**string)) = &collectionID
	*(dest[11].(*json.RawMessage)) = json.RawMessage(`{"palette":[],"typography":[]}`)
	*(dest[12].(*bool)) = true
	*(dest[13].(*json.RawMessage)) = json.RawMessage(`[]`)
	*(dest[14].(*json.RawMessage)) = json.RawMessage(`[]`)
	*(dest[15].(*time.Time)) = now
	*(dest[16].(*time.Time)) = now
	return nil
}

func TestTemplateMetadataAPIKeyRouteRequiresReadScope(t *testing.T) {
	route, designID, ok := matchAPIKeyRoute(http.MethodGet, "/api/v1/templates/template-id")
	if !ok || route.scope != apikeys.ScopeRead || designID != "" {
		t.Fatalf("template metadata route mismatch: ok=%v scope=%q designID=%q", ok, route.scope, designID)
	}
}

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

func TestRenderTemplatePreviewEmbedsOnlyExportedPage(t *testing.T) {
	var source bytes.Buffer
	pixel := image.NewRGBA(image.Rect(0, 0, 1, 1))
	pixel.SetRGBA(0, 0, color.RGBA{R: 255, A: 255})
	if err := png.Encode(&source, pixel); err != nil {
		t.Fatal(err)
	}
	pages := make([]any, 230)
	for i := range pages {
		pages[i] = map[string]any{
			"width": 100.0, "height": 100.0,
			"children": []any{map[string]any{
				"type": "image", "source": map[string]any{"assetId": "shared-background"},
				"size": map[string]any{"width": 100.0, "height": 100.0},
			}},
		}
	}
	fetches := 0
	fetch := func(string) ([]byte, string, error) {
		fetches++
		return source.Bytes(), "image/png", nil
	}

	data, err := renderTemplatePreview(map[string]any{"pages": pages}, fetch)
	if err != nil {
		t.Fatal(err)
	}
	if fetches != 1 {
		t.Fatalf("single-page preview fetched assets %d times, want 1", fetches)
	}
	preview, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	r, g, b, _ := preview.At(12, 12).RGBA()
	if r != 65535 || g != 0 || b != 0 {
		t.Fatalf("exported page lost its background: RGB = %d, %d, %d", r, g, b)
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

func TestTemplateOverlayAPIKeyRouteRequiresExportScope(t *testing.T) {
	route, designID, ok := matchAPIKeyRoute(http.MethodGet, "/api/v1/templates/template-id/render-overlay.png")
	if !ok {
		t.Fatal("template overlay route is not available to API keys")
	}
	if route.scope != apikeys.ScopeExport || designID != "" {
		t.Fatalf("template overlay route = scope %q design %q", route.scope, designID)
	}
}

func TestRenderTemplateOverlayPreservesAlphaAndEmbedsOnlyFirstPage(t *testing.T) {
	var source bytes.Buffer
	pixel := image.NewRGBA(image.Rect(0, 0, 1, 1))
	pixel.SetRGBA(0, 0, color.RGBA{R: 255, A: 255})
	if err := png.Encode(&source, pixel); err != nil {
		t.Fatal(err)
	}
	file := map[string]any{"pages": []any{
		map[string]any{
			"width": 100.0, "height": 200.0,
			"background": map[string]any{"type": "solid", "color": map[string]any{"srgb": map[string]any{"r": 1.0, "g": 1.0, "b": 1.0, "a": 1.0}}},
			"children": []any{map[string]any{
				"type": "image", "source": map[string]any{"assetId": "foreground"},
				"size":      map[string]any{"width": 40.0, "height": 40.0},
				"transform": map[string]any{"x": 20.0, "y": 20.0},
			}},
		},
		map[string]any{"children": []any{map[string]any{
			"type": "image", "source": map[string]any{"assetId": "unused-page"},
		}}},
	}}
	fetches := 0
	fetch := func(assetID string) ([]byte, string, error) {
		fetches++
		if assetID != "foreground" {
			t.Fatalf("unexpected asset from unused page: %q", assetID)
		}
		return source.Bytes(), "image/png", nil
	}
	data, err := renderTemplateOverlay(file, fetch)
	if err != nil {
		t.Fatal(err)
	}
	img, err := png.Decode(bytes.NewReader(data))
	if err != nil {
		t.Fatal(err)
	}
	if fetches != 1 || img.Bounds().Dx() != 25 || img.Bounds().Dy() != 50 {
		t.Fatalf("overlay fetched %d assets, dimensions = %v", fetches, img.Bounds())
	}
	if _, _, _, alpha := img.At(0, 0).RGBA(); alpha != 0 {
		t.Fatalf("page background is opaque: alpha = %d", alpha)
	}
	if r, g, b, alpha := img.At(10, 10).RGBA(); r != 65535 || g != 0 || b != 0 || alpha != 65535 {
		t.Fatalf("foreground lost color or alpha: RGBA = %d, %d, %d, %d", r, g, b, alpha)
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

func TestTemplateCatalogIsPublicAndNeedsNoWorkspaceID(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/catalog", nil)

	templatesCatalogHandler(service).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Categories []templates.Category `json:"categories"`
	}
	if err := json.NewDecoder(recorder.Body).Decode(&response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(response.Categories) != 1 || response.Categories[0].Name != "内容报价" || response.Categories[0].Templates == nil {
		t.Fatalf("response = %+v", response)
	}
}

func TestTemplateCategoriesArePublicAndNeedNoAPIKey(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/categories", nil)

	templatesCategoriesHandler(service).ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Categories []templates.PublicCategory `json:"categories"`
	}
	if err := json.NewDecoder(recorder.Body).Decode(&response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(response.Categories) != 1 || response.Categories[0].ID != "category-1" || response.Categories[0].Name != "内容报价" {
		t.Fatalf("response = %+v", response)
	}
}

func TestTemplatesByCategoryArePublicAndNeedNoAPIKey(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	router := chi.NewRouter()
	router.Get("/api/v1/templates/categories/{id}/templates", templatesByCategoryHandler(service))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/categories/category-1/templates", nil)

	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Templates []templates.PublicTemplate `json:"templates"`
	}
	if err := json.NewDecoder(recorder.Body).Decode(&response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(response.Templates) != 1 || response.Templates[0].IsHandwrittenQuoteTemplate != 1 {
		t.Fatalf("response = %+v", response)
	}
}

func TestTemplatesByCategoryReturnsNotFoundForUnknownCategory(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	router := chi.NewRouter()
	router.Get("/api/v1/templates/categories/{id}/templates", templatesByCategoryHandler(service))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/categories/unknown/templates", nil)

	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusNotFound {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
}

func TestTemplatesByCategoriesAcceptsMultipleIDsWithoutAPIKey(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	router := chi.NewRouter()
	router.Get("/api/v1/templates/categories/templates", templatesByCategoriesHandler(service))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/categories/templates?categoryIds=category-1,category-1", nil)

	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Categories []templates.Category `json:"categories"`
	}
	if err := json.NewDecoder(recorder.Body).Decode(&response); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(response.Categories) != 2 || response.Categories[0].ID != "category-1" {
		t.Fatalf("response = %+v", response)
	}
}

func TestTemplatesByCategoriesRequiresIDs(t *testing.T) {
	service := templates.NewService(publicCatalogDB{}, nil, nil)
	router := chi.NewRouter()
	router.Get("/api/v1/templates/categories/templates", templatesByCategoriesHandler(service))
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/templates/categories/templates", nil)

	router.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("status = %d body=%s", recorder.Code, recorder.Body.String())
	}
}
