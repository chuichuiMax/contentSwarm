package httpapi

import (
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestManagedAuthPagesRedirectToContentSwarm(t *testing.T) {
	router := NewRouter(Deps{
		Logger:          slog.New(slog.NewTextHandler(io.Discard, nil)),
		Version:         "test",
		ManagedAuth:     true,
		ContentSwarmURL: "http://127.0.0.1:5173",
	})

	for _, path := range []string{"/login", "/login/", "/signup", "/reset-password/"} {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		res := httptest.NewRecorder()
		router.ServeHTTP(res, req)
		if res.Code != http.StatusFound || res.Header().Get("Location") != "http://127.0.0.1:5173/hycanvas" {
			t.Fatalf("%s: status=%d location=%q", path, res.Code, res.Header().Get("Location"))
		}
	}
}

func TestManagedAuthPagesKeepTheRequestLoopbackHostname(t *testing.T) {
	router := NewRouter(Deps{
		Logger:          slog.New(slog.NewTextHandler(io.Discard, nil)),
		Version:         "test",
		ManagedAuth:     true,
		ContentSwarmURL: "http://127.0.0.1:5173",
	})

	for _, tc := range []struct {
		host     string
		expected string
	}{
		{host: "localhost:8005", expected: "http://localhost:5173/hycanvas"},
		{host: "127.0.0.1:8005", expected: "http://127.0.0.1:5173/hycanvas"},
	} {
		req := httptest.NewRequest(http.MethodGet, "/login", nil)
		req.Host = tc.host
		res := httptest.NewRecorder()

		router.ServeHTTP(res, req)

		if res.Code != http.StatusFound || res.Header().Get("Location") != tc.expected {
			t.Fatalf("host %s: status=%d location=%q", tc.host, res.Code, res.Header().Get("Location"))
		}
	}
}
