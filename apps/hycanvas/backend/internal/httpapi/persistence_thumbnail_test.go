package httpapi

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestUpdateDesignThumbnailRejectsInvalidPayload(t *testing.T) {
	req := httptest.NewRequest(http.MethodPut, "/designs/d1/thumbnail", strings.NewReader(`{"thumbnail":"not-an-image"}`))
	rec := httptest.NewRecorder()

	updateDesignThumbnailHandler(nil, nil).ServeHTTP(rec, req)

	if rec.Code != http.StatusUnprocessableEntity {
		t.Fatalf("status = %d, want %d", rec.Code, http.StatusUnprocessableEntity)
	}
}
