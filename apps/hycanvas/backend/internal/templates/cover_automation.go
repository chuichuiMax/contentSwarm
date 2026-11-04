package templates

import "hycanvas/backend/internal/persistence"

func markContentSwarmAutomationCover(file map[string]any) {
	meta := asObj(file["meta"])
	if meta == nil {
		meta = map[string]any{}
		file["meta"] = meta
	}
	meta["kind"] = persistence.DocKindContentSwarmCover
}