package persistence

import "testing"

func TestCompactOversizedFontsKeepsNormalCustomFont(t *testing.T) {
	file := DesignFile{"fonts": []any{map[string]any{"family": "Custom", "url": "data:font/ttf;base64,small"}}}
	if CompactOversizedFonts(file) {
		t.Fatal("normal custom font was compacted")
	}
	font := file["fonts"].([]any)[0].(map[string]any)
	if font["url"] == nil {
		t.Fatal("normal custom font URL was removed")
	}
}

func TestCompactOversizedFontsDropsOnlyInlinePayloads(t *testing.T) {
	large := "data:application/octet-stream;base64," + string(make([]byte, maxEmbeddedFontURLBytes+1))
	file := DesignFile{"fonts": []any{
		map[string]any{"id": "embedded", "family": "Source Han Sans", "source": "upload", "url": large},
		map[string]any{"id": "web", "family": "Inter", "source": "google", "url": "https://fonts.example/inter.css"},
	}}
	if !CompactOversizedFonts(file) {
		t.Fatal("oversized font collection was not compacted")
	}
	fonts := file["fonts"].([]any)
	embedded := fonts[0].(map[string]any)
	if _, ok := embedded["url"]; ok {
		t.Fatal("inline payload was retained")
	}
	if embedded["family"] != "Source Han Sans" || embedded["id"] != "embedded" {
		t.Fatal("font reference metadata was not preserved")
	}
	if fonts[1].(map[string]any)["url"] != "https://fonts.example/inter.css" {
		t.Fatal("non-inline font URL was removed")
	}
}
