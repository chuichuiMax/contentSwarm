package render

import "testing"

func TestEffectiveFontWeightSupportsTemplateStyleFormats(t *testing.T) {
	tests := []struct {
		name  string
		style map[string]any
		want  int
	}{
		{name: "axis wins", style: map[string]any{"fontStyle": "Regular", "axes": map[string]any{"wght": 650.0}}, want: 650},
		{name: "numeric font weight", style: map[string]any{"fontWeight": 800.0}, want: 800},
		{name: "bold style", style: map[string]any{"fontStyle": "Bold"}, want: 700},
		{name: "semi bold italic style", style: map[string]any{"fontStyle": "Semi Bold Italic"}, want: 600},
		{name: "regular default", style: map[string]any{"fontStyle": "Regular"}, want: 400},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := effectiveFontWeight(tt.style); got != tt.want {
				t.Fatalf("effectiveFontWeight() = %d, want %d", got, tt.want)
			}
		})
	}
}

func TestEffectiveFontItalicSupportsTemplateStyleFormats(t *testing.T) {
	for _, style := range []map[string]any{
		{"fontStyle": "ExtraBold Italic"},
		{"axes": map[string]any{"ital": 1.0}},
		{"axes": map[string]any{"slnt": -8.0}},
	} {
		if !effectiveFontItalic(style) {
			t.Fatalf("effectiveFontItalic(%#v) = false", style)
		}
	}
	if effectiveFontItalic(map[string]any{"fontStyle": "Regular"}) {
		t.Fatal("regular style should not be italic")
	}
}
