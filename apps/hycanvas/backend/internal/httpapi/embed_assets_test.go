package httpapi

import (
	"errors"
	"strings"
	"testing"
)

// TestEmbedDesignFileAssets confirms image nodes, image fills, and nested images
// get their bytes inlined as data URLs across the file, without mutating the
// input (so the PNG/JPEG design exporter renders images referenced by asset id).
func TestEmbedDesignFileAssets(t *testing.T) {
	fetch := func(aid string) ([]byte, string, error) {
		if aid == "inline" {
			return nil, "", errors.New("inline assets are not uploads")
		}
		return []byte("PNG-" + aid), "image/png", nil
	}
	file := map[string]any{
		"assets": []any{
			map[string]any{"id": "inline", "kind": "image", "url": "data:image/jpeg;base64,aW5saW5l", "mime": "image/jpeg"},
		},
		"pages": []any{map[string]any{
			"children": []any{
				map[string]any{"type": "image", "source": map[string]any{"assetId": "a1"}},
				map[string]any{"type": "image", "source": map[string]any{"assetId": "inline"}},
				map[string]any{"type": "shape", "shape": "rect", "fills": []any{
					map[string]any{"type": "image", "source": map[string]any{"assetId": "a2"}},
				}},
				map[string]any{"type": "group", "children": []any{
					map[string]any{"type": "image", "source": map[string]any{"assetId": "a3"}},
				}},
			},
		}},
	}
	out := embedDesignFileAssets(fetch, file, 0)
	kids := out["pages"].([]any)[0].(map[string]any)["children"].([]any)

	embedded := func(m map[string]any, what string) {
		if src, _ := m["src"].(string); !strings.HasPrefix(src, "data:image/png;base64,") {
			t.Fatalf("%s not embedded: %v", what, m["src"])
		}
	}
	embedded(kids[0].(map[string]any), "image node")
	if got := kids[1].(map[string]any)["src"]; got != "data:image/jpeg;base64,aW5saW5l" {
		t.Fatalf("inline image asset not embedded: %v", got)
	}
	embedded(kids[2].(map[string]any)["fills"].([]any)[0].(map[string]any), "image fill")
	embedded(kids[3].(map[string]any)["children"].([]any)[0].(map[string]any), "nested image")

	// Input is not mutated.
	orig := file["pages"].([]any)[0].(map[string]any)["children"].([]any)[0].(map[string]any)
	if _, has := orig["src"]; has {
		t.Fatalf("original file was mutated")
	}
	// Inline design assets still render when no uploads service is available.
	withoutUploads := embedDesignFileAssets(nil, file, 0)
	inlineNode := withoutUploads["pages"].([]any)[0].(map[string]any)["children"].([]any)[1].(map[string]any)
	if got := inlineNode["src"]; got != "data:image/jpeg;base64,aW5saW5l" {
		t.Fatalf("inline asset should render without uploads service: %v", got)
	}
}

func TestEmbedDesignFileAssetsSelectsPageWithoutMutatingOtherPages(t *testing.T) {
	pages := []any{}
	for _, id := range []string{"first", "second", "third"} {
		pages = append(pages, map[string]any{"children": []any{
			map[string]any{"type": "image", "source": map[string]any{"assetId": id}},
		}})
	}
	file := map[string]any{"pages": pages}
	fetched := []string{}
	fetch := func(id string) ([]byte, string, error) {
		fetched = append(fetched, id)
		return []byte(id), "image/png", nil
	}

	out := embedDesignFileAssets(fetch, file, 1)
	if len(fetched) != 1 || fetched[0] != "second" {
		t.Fatalf("fetched %v, want only the selected page's image", fetched)
	}
	for i, page := range out["pages"].([]any) {
		node := page.(map[string]any)["children"].([]any)[0].(map[string]any)
		_, embedded := node["src"]
		if embedded != (i == 1) {
			t.Fatalf("page %d embedded = %v", i, embedded)
		}
		original := pages[i].(map[string]any)["children"].([]any)[0].(map[string]any)
		if _, mutated := original["src"]; mutated {
			t.Fatalf("input page %d was mutated", i)
		}
	}
	for _, index := range []int{-1, len(pages)} {
		embedDesignFileAssets(fetch, file, index)
	}
	if len(fetched) != 1 {
		t.Fatal("invalid page indices must not fetch assets")
	}
}
