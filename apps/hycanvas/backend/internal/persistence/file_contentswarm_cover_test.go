package persistence

import "testing"

func TestFileMarksContentSwarmCover(t *testing.T) {
	t.Run("meta kind", func(t *testing.T) {
		file := DesignFile{"meta": map[string]any{"kind": DocKindContentSwarmCover}, "pages": []any{}}
		if !FileMarksContentSwarmCover(file) {
			t.Fatal("expected meta.kind match")
		}
	})
	t.Run("background layer id", func(t *testing.T) {
		file := DesignFile{
			"pages": []any{
				map[string]any{
					"children": []any{
						map[string]any{"id": "contentswarm-background-0", "type": "image"},
					},
				},
			},
		}
		if !FileMarksContentSwarmCover(file) {
			t.Fatal("expected background id match")
		}
	})
	t.Run("plain design", func(t *testing.T) {
		file := DesignFile{
			"pages": []any{
				map[string]any{
					"children": []any{
						map[string]any{"id": "title", "type": "text"},
					},
				},
			},
		}
		if FileMarksContentSwarmCover(file) {
			t.Fatal("expected no match")
		}
	})
}
