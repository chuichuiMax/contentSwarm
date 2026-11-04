package httpapi

import (
	"net/http"
	"net/url"
	"path/filepath"
	"strings"

	"github.com/go-chi/chi/v5"

	"hycanvas/backend/internal/accounts"
	"hycanvas/backend/internal/render"
)

func mountFonts(api chi.Router, acct *accounts.Service) {
	api.With(requireAuth(acct)).Get("/fonts", packagedFontsHandler)
	api.With(requireAuth(acct)).Get("/fonts/files/{name}", packagedFontFileHandler)
}

func packagedFontsHandler(w http.ResponseWriter, _ *http.Request) {
	catalog := render.FontCatalog()
	out := make([]map[string]any, 0, len(catalog))
	for _, entry := range catalog {
		files := make([]map[string]any, 0, len(entry.Files))
		for _, file := range entry.Files {
			files = append(files, map[string]any{
				"weight": file.Weight,
				"url":    "/api/v1/fonts/files/" + url.PathEscape(file.Name),
			})
		}
		out = append(out, map[string]any{
			"family":  entry.Family,
			"label":   entry.Label,
			"weights": entry.Weights,
			"files":   files,
		})
	}
	writeJSON(w, http.StatusOK, out)
}

func packagedFontFileHandler(w http.ResponseWriter, r *http.Request) {
	name := chi.URLParam(r, "name")
	path, ok := render.FontFilePath(name)
	if !ok {
		http.NotFound(w, r)
		return
	}
	switch strings.ToLower(filepath.Ext(path)) {
	case ".otf":
		w.Header().Set("Content-Type", "font/otf")
	case ".ttf":
		w.Header().Set("Content-Type", "font/ttf")
	default:
		w.Header().Set("Content-Type", "application/octet-stream")
	}
	w.Header().Set("Cache-Control", "private, max-age=86400")
	http.ServeFile(w, r, path)
}
