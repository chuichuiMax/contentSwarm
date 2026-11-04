package render

import (
	"os"
	"path/filepath"
	"testing"

	"golang.org/x/image/font/gofont/gobold"
	"golang.org/x/image/font/gofont/goregular"
	"golang.org/x/image/font/opentype"
)

func isolateFontRegistry(t *testing.T) {
	t.Helper()
	fontMu.Lock()
	previous := fontReg
	previousCatalog := fontCatalog
	previousDir := fontDir
	fontReg = map[fontKey]*opentype.Font{}
	fontCatalog = nil
	fontDir = ""
	fontMu.Unlock()
	t.Cleanup(func() {
		fontMu.Lock()
		fontReg = previous
		fontCatalog = previousCatalog
		fontDir = previousDir
		fontMu.Unlock()
	})
}

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

func TestCJKCollectionFamilyUsesRegionalSansAndSerifNames(t *testing.T) {
	regions := []string{"JP", "KR", "SC", "TC", "HK"}
	if got := cjkCollectionFamily("NotoSansCJK", 2, regions); got != "NotoSansSC" {
		t.Fatalf("sans family = %q", got)
	}
	if got := cjkCollectionFamily("NotoSerifCJK", 2, regions); got != "NotoSerifSC" {
		t.Fatalf("serif family = %q", got)
	}
	if got := cjkCollectionFamily("NotoSansCJK", 7, regions); got != "NotoSansCJK7" {
		t.Fatalf("extra collection face = %q", got)
	}
}

func TestLookupFontMatchesPackagedTemplateNames(t *testing.T) {
	isolateFontRegistry(t)
	regular, err := opentype.Parse(goregular.TTF)
	if err != nil {
		t.Fatal(err)
	}
	bold, err := opentype.Parse(gobold.TTF)
	if err != nil {
		t.Fatal(err)
	}
	registerParsedFont("NotoSansSC", 400, regular)
	registerParsedFont("NotoSerifSC", 700, bold)

	if got := lookupFont("Noto Sans SC", 400); got != regular {
		t.Fatal("Noto Sans SC did not resolve to the packaged sans face")
	}
	if got := lookupFont("Noto Serif SC", 700); got != bold {
		t.Fatal("Noto Serif SC did not resolve to the packaged serif face")
	}
}

func TestSystemFontUsesOneDeterministicCJKFamilyAndNearestWeight(t *testing.T) {
	isolateFontRegistry(t)
	regular, err := opentype.Parse(goregular.TTF)
	if err != nil {
		t.Fatal(err)
	}
	bold, err := opentype.Parse(gobold.TTF)
	if err != nil {
		t.Fatal(err)
	}
	registerParsedFont("NotoSansSC", 400, regular)
	registerParsedFont("NotoSansSC", 700, bold)

	if got := lookupFont("system", 900); got != bold {
		t.Fatal("system title did not resolve to the nearest Simplified Chinese bold face")
	}
	if got := lookupFont("system-ui", 400); got != regular {
		t.Fatal("system-ui body text did not resolve to the Simplified Chinese regular face")
	}
}

func TestFallbackFontOrderPrefersSansAndIsStable(t *testing.T) {
	isolateFontRegistry(t)
	regular, err := opentype.Parse(goregular.TTF)
	if err != nil {
		t.Fatal(err)
	}
	bold, err := opentype.Parse(gobold.TTF)
	if err != nil {
		t.Fatal(err)
	}
	registerParsedFont("NotoSerifCJK0", 700, regular)
	registerParsedFont("NotoSansSC", 700, bold)

	for range 20 {
		if got := fontCovering('A', "Missing Family", 900); got != bold {
			t.Fatal("fallback selection mixed serif and sans faces across glyphs")
		}
	}
	if fallbackFamilyRank('长', "notosanssc") >= fallbackFamilyRank('长', "notosansjp") {
		t.Fatal("Simplified Chinese glyphs should prefer Noto Sans SC")
	}
}

func TestLoadFontDirCatalogsFilenameFamily(t *testing.T) {
	isolateFontRegistry(t)
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "DouyinSans-700.ttf"), goregular.TTF, 0o644); err != nil {
		t.Fatal(err)
	}
	count, errs := LoadFontDir(dir)
	if count != 1 || len(errs) != 0 {
		t.Fatalf("LoadFontDir() = %d, %v", count, errs)
	}
	catalog := FontCatalog()
	if len(catalog) != 1 || catalog[0].Family != "DouyinSans" || len(catalog[0].Weights) != 1 || catalog[0].Weights[0] != 700 {
		t.Fatalf("catalog = %+v", catalog)
	}
	if catalog[0].Label == "" {
		t.Fatal("catalog label is empty")
	}
	if _, ok := FontFilePath("DouyinSans-700.ttf"); !ok {
		t.Fatal("installed font file was not published")
	}
	if _, ok := FontFilePath("../DouyinSans-700.ttf"); ok {
		t.Fatal("font file path escaped the font directory")
	}
	if got, ok := CanonicalFamily("Douyin Sans"); !ok || got != "DouyinSans" {
		t.Fatalf("CanonicalFamily() = %q, %v", got, ok)
	}
}
