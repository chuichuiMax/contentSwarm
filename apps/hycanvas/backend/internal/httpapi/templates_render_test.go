package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"image/png"
	"net/http"
	"net/http/httptest"
	"testing"

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
	return r.collectionQuery
}
func (r *publicCatalogRows) Scan(dest ...any) error {
	*(dest[0].(*string)) = "category-1"
	*(dest[1].(*string)) = "workspace-1"
	*(dest[2].(*string)) = "内容报价"
	return nil
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
	if response.Templates == nil || len(response.Templates) != 0 {
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
