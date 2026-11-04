// Optional real-font registry for the raster exporter. By default text draws in
// the embedded Arial-metric fallback (Liberation Sans, see fallbackfont.go); a
// host can register actual font files (e.g. the design's Google Fonts) so
// server-side rasters are glyph-true. Lookup never fails: an unregistered family
// falls back to the embedded font.
package render

import (
	"encoding/binary"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"unicode"
	"unicode/utf16"

	"golang.org/x/image/font"
	"golang.org/x/image/font/opentype"
)

type fontKey struct {
	family string
	weight int
}

var (
	fontMu      sync.RWMutex
	fontReg     = map[fontKey]*opentype.Font{}
	fontDir     string
	fontCatalog []catalogFace
)

// catalogFace is one weight of a font the editor may offer. File is the
// basename in FONTS_DIR; collection faces that the browser already loads as
// web fonts leave it empty.
type catalogFace struct {
	Family string
	Label  string
	Weight int
	File   string
}

// FontFile is one downloadable face for a family.
type FontFile struct {
	Weight int
	Name   string
}

// FontCatalogEntry is a family the cover editor can select.
type FontCatalogEntry struct {
	Family  string
	Label   string
	Weights []int
	Files   []FontFile
}

var namedFontWeights = map[string]int{
	"thin":       100,
	"extralight": 200,
	"ultralight": 200,
	"light":      300,
	"book":       400,
	"regular":    400,
	"normal":     400,
	"medium":     500,
	"semibold":   600,
	"demibold":   600,
	"bold":       700,
	"extrabold":  800,
	"ultrabold":  800,
	"black":      900,
	"heavy":      900,
}

// effectiveFontWeight mirrors the browser renderer's compatibility rules.
// Modern files store weight on axes.wght, while imported and older templates
// may only carry a named fontStyle such as Bold or SemiBold.
func effectiveFontWeight(style map[string]any) int {
	if weight := int(asNum(asObj(style["axes"])["wght"])); weight > 0 {
		return weight
	}
	for _, key := range []string{"fontWeight", "weight"} {
		if weight := int(asNum(style[key])); weight > 0 {
			return weight
		}
	}
	name := strings.ToLower(asStr(style["fontStyle"]))
	name = strings.NewReplacer("italic", "", "oblique", "", " ", "", "-", "", "_", "").Replace(name)
	if weight := namedFontWeights[name]; weight > 0 {
		return weight
	}
	return 400
}

func effectiveFontItalic(style map[string]any) bool {
	axes := asObj(style["axes"])
	return asNum(axes["ital"]) >= 0.5 || asNum(axes["slnt"]) < 0 ||
		strings.Contains(strings.ToLower(asStr(style["fontStyle"])), "italic") ||
		strings.Contains(strings.ToLower(asStr(style["fontStyle"])), "oblique") || asBool(style["italic"])
}

// normFamily canonicalizes a family name for lookup ("Space Mono" == "SpaceMono").
func normFamily(f string) string {
	return strings.ToLower(strings.ReplaceAll(f, " ", ""))
}

// RegisterFont parses and registers font bytes for a family/weight pair.
func RegisterFont(family string, weight int, data []byte) error {
	f, err := opentype.Parse(data)
	if err != nil {
		return err
	}
	registerParsedFont(family, weight, f)
	return nil
}

func registerParsedFont(family string, weight int, f *opentype.Font) {
	fontMu.Lock()
	defer fontMu.Unlock()
	fontReg[fontKey{normFamily(family), weight}] = f
}

// lookupFont returns the registered font nearest in weight for the family.
// Generic browser families resolve to one stable Simplified Chinese sans face
// so a single run cannot mix random CJK collection members glyph by glyph.
func lookupFont(family string, weight int) *opentype.Font {
	fontMu.RLock()
	defer fontMu.RUnlock()
	fam := normFamily(family)
	if best := lookupFamilyFontLocked(fam, weight); best != nil {
		return best
	}
	if fam == "" || fam == "system" || fam == "systemui" || fam == "system-ui" || fam == "sansserif" || fam == "sans-serif" {
		for _, fallback := range []string{"notosanssc", "notosansjp", "notosanskr", "notosanstc", "notosanshk"} {
			if best := lookupFamilyFontLocked(fallback, weight); best != nil {
				return best
			}
		}
	}
	return nil
}

func lookupFamilyFontLocked(family string, weight int) *opentype.Font {
	var best *opentype.Font
	bestDist := 1 << 30
	bestWeight := 0
	for k, f := range fontReg {
		if k.family != family {
			continue
		}
		d := k.weight - weight
		if d < 0 {
			d = -d
		}
		preferTie := d == bestDist && ((weight >= 500 && k.weight > bestWeight) || (weight < 500 && (bestWeight == 0 || k.weight < bestWeight)))
		if d < bestDist || preferTie {
			bestDist = d
			bestWeight = k.weight
			best = f
		}
	}
	return best
}

// --- glyph coverage and script fallback --------------------------------------
//
// The embedded fallback (Liberation Sans) covers Latin, Greek and Cyrillic and
// nothing else: no Hebrew, no Arabic, no CJK, no Indic. Text in those scripts
// used to draw as NOTHING, because an uncovered rune was silently skipped, so a
// design exported blank where the canvas showed words. Registering fonts is how
// an operator fixes that, and this picks whichever registered font actually
// covers each rune.

// coversRune reports whether a font has a glyph for r at a nominal size.
func coversRune(f *opentype.Font, r rune) bool {
	face, err := opentype.NewFace(f, &opentype.FaceOptions{Size: 16, DPI: 72})
	if err != nil {
		return false
	}
	defer face.Close()
	_, ok := face.GlyphAdvance(r)
	return ok
}

// fontCovering returns a registered font with a glyph for r, preferring the
// requested family so a design that names a font it registered keeps it.
func fontCovering(r rune, preferFamily string, weight int) *opentype.Font {
	if f := lookupFont(preferFamily, weight); f != nil && coversRune(f, r) {
		return f
	}
	fontMu.RLock()
	defer fontMu.RUnlock()
	keys := make([]fontKey, 0, len(fontReg))
	for key := range fontReg {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		left, right := keys[i], keys[j]
		leftFamily, rightFamily := fallbackFamilyRank(r, left.family), fallbackFamilyRank(r, right.family)
		if leftFamily != rightFamily {
			return leftFamily < rightFamily
		}
		leftWeight, rightWeight := left.weight-weight, right.weight-weight
		if leftWeight < 0 {
			leftWeight = -leftWeight
		}
		if rightWeight < 0 {
			rightWeight = -rightWeight
		}
		if leftWeight != rightWeight {
			return leftWeight < rightWeight
		}
		if left.weight != right.weight {
			if weight >= 500 {
				return left.weight > right.weight
			}
			return left.weight < right.weight
		}
		return left.family < right.family
	})
	for _, key := range keys {
		f := fontReg[key]
		if coversRune(f, r) {
			return f
		}
	}
	return nil
}

func fallbackFamilyRank(r rune, family string) int {
	preferred := []string{"notosanssc", "notosansjp", "notosanskr", "notosanstc", "notosanshk"}
	switch {
	case unicode.In(r, unicode.Hiragana, unicode.Katakana):
		preferred = []string{"notosansjp", "notosanssc", "notosanstc", "notosanshk", "notosanskr"}
	case unicode.In(r, unicode.Hangul):
		preferred = []string{"notosanskr", "notosanssc", "notosansjp", "notosanstc", "notosanshk"}
	}
	for index, candidate := range preferred {
		if family == candidate {
			return index
		}
	}
	if strings.Contains(family, "mono") {
		return 30
	}
	if strings.Contains(family, "serif") {
		return 20
	}
	return 10
}

// FontCatalog groups the installed cover fonts by the family name a design
// stores. Noto Sans SC and Noto Serif SC stay first; other families follow by
// their display label.
func FontCatalog() []FontCatalogEntry {
	fontMu.RLock()
	defer fontMu.RUnlock()
	grouped := map[string]*FontCatalogEntry{}
	order := []string{}
	for _, face := range fontCatalog {
		entry := grouped[face.Family]
		if entry == nil {
			entry = &FontCatalogEntry{Family: face.Family, Label: face.Label}
			grouped[face.Family] = entry
			order = append(order, face.Family)
		}
		if face.Weight > 0 && !containsInt(entry.Weights, face.Weight) {
			entry.Weights = append(entry.Weights, face.Weight)
		}
		if face.File != "" && !containsFontFile(entry.Files, face.File) {
			entry.Files = append(entry.Files, FontFile{Weight: face.Weight, Name: face.File})
		}
	}
	out := make([]FontCatalogEntry, 0, len(order))
	for _, family := range order {
		entry := *grouped[family]
		sort.Ints(entry.Weights)
		sort.Slice(entry.Files, func(i, j int) bool { return entry.Files[i].Weight < entry.Files[j].Weight })
		out = append(out, entry)
	}
	sort.SliceStable(out, func(i, j int) bool {
		return fontCatalogRank(out[i].Family) < fontCatalogRank(out[j].Family) ||
			(fontCatalogRank(out[i].Family) == fontCatalogRank(out[j].Family) && out[i].Label < out[j].Label)
	})
	return out
}

func fontCatalogRank(family string) int {
	switch family {
	case "Noto Sans SC":
		return 0
	case "Noto Serif SC":
		return 1
	default:
		return 2
	}
}

func containsInt(values []int, want int) bool {
	for _, value := range values {
		if value == want {
			return true
		}
	}
	return false
}

func containsFontFile(files []FontFile, name string) bool {
	for _, file := range files {
		if file.Name == name {
			return true
		}
	}
	return false
}

// CanonicalFamily returns the design font name for a family installed in the
// cover font package. Matching ignores spaces and case.
func CanonicalFamily(family string) (string, bool) {
	key := normFamily(family)
	if key == "" {
		return "", false
	}
	fontMu.RLock()
	defer fontMu.RUnlock()
	for _, face := range fontCatalog {
		if normFamily(face.Family) == key {
			return face.Family, true
		}
	}
	return "", false
}

// FontFilePath returns the installed file for a catalog basename.
func FontFilePath(name string) (string, bool) {
	base := filepath.Base(name)
	if base != name || base == "." || base == string(filepath.Separator) {
		return "", false
	}
	fontMu.RLock()
	defer fontMu.RUnlock()
	if fontDir == "" {
		return "", false
	}
	for _, face := range fontCatalog {
		if face.File == base {
			return filepath.Join(fontDir, base), true
		}
	}
	return "", false
}

func rememberFace(family, label string, weight int, file string) {
	if strings.TrimSpace(family) == "" {
		return
	}
	if strings.TrimSpace(label) == "" {
		label = family
	}
	fontCatalog = append(fontCatalog, catalogFace{Family: family, Label: label, Weight: weight, File: file})
}

// RegisteredFamilies lists the registered family names, for startup logging so
// an operator can see what coverage the server actually has.
func RegisteredFamilies() []string {
	fontMu.RLock()
	defer fontMu.RUnlock()
	seen := map[string]bool{}
	out := []string{}
	for k := range fontReg {
		if !seen[k.family] {
			seen[k.family] = true
			out = append(out, k.family)
		}
	}
	return out
}

// cjkCollectionFamily names a face inside a Noto CJK collection. The Debian
// packages ship Sans and Serif as regional faces in a fixed order. Templates
// address them as "Noto Sans SC" and "Noto Serif SC", which lookup compares
// after removing spaces.
func cjkCollectionFamily(family string, index int, regions []string) string {
	script := ""
	switch family {
	case "NotoSansCJK":
		script = "Sans"
	case "NotoSerifCJK":
		script = "Serif"
	}
	if script != "" && index < len(regions) {
		return "Noto" + script + regions[index]
	}
	return fmt.Sprintf("%s%d", family, index)
}

// LoadFontDir registers every font file in dir, so a self-hoster can drop in
// the scripts their users write in. Filenames follow the convention the
// template renderer already uses: "Family-Weight.ttf" (weight optional,
// defaulting to 400), for example "NotoSansHebrew-700.ttf" or "NotoSansArabic.ttf".
// Returns the number registered; a file that fails to parse is skipped with an
// error rather than aborting the rest.
func LoadFontDir(dir string) (int, []error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return 0, []error{fmt.Errorf("read font dir: %w", err)}
	}
	fontMu.Lock()
	fontDir = dir
	fontCatalog = nil
	fontMu.Unlock()
	var errs []error
	n := 0
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		ext := strings.ToLower(filepath.Ext(e.Name()))
		if ext != ".ttf" && ext != ".otf" && ext != ".ttc" && ext != ".otc" {
			continue
		}
		data, err := os.ReadFile(filepath.Join(dir, e.Name()))
		if err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", e.Name(), err))
			continue
		}
		base := strings.TrimSuffix(e.Name(), filepath.Ext(e.Name()))
		family, weight := base, 400
		if i := strings.LastIndex(base, "-"); i > 0 {
			if w, convErr := strconv.Atoi(base[i+1:]); convErr == nil {
				family, weight = base[:i], w
			}
		}
		if strings.HasSuffix(family, "-Bold") {
			family, weight = strings.TrimSuffix(family, "-Bold"), 700
		} else if strings.HasSuffix(family, "-Regular") {
			family = strings.TrimSuffix(family, "-Regular")
		}
		if ext == ".ttc" || ext == ".otc" {
			collection, parseErr := opentype.ParseCollection(data)
			if parseErr != nil {
				errs = append(errs, fmt.Errorf("%s: %w", e.Name(), parseErr))
				continue
			}
			regions := []string{"JP", "KR", "SC", "TC", "HK"}
			for i := 0; i < collection.NumFonts(); i++ {
				font, fontErr := collection.Font(i)
				if fontErr != nil {
					errs = append(errs, fmt.Errorf("%s font %d: %w", e.Name(), i, fontErr))
					continue
				}
				registeredFamily := cjkCollectionFamily(family, i, regions)
				registerParsedFont(registeredFamily, weight, font)
				rememberEditorFace(registeredFamily, "", weight, "")
				n++
			}
			continue
		}
		if err := RegisterFont(family, weight, data); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", e.Name(), err))
			continue
		}
		rememberEditorFace(family, fontLabel(data), weight, e.Name())
		n++
	}
	return n, errs
}

// rememberEditorFace publishes a face to the editor. Regional Noto collections
// collapse to the two Simplified Chinese families the covers already use.
func rememberEditorFace(family, label string, weight int, file string) {
	switch family {
	case "NotoSansSC":
		rememberFace("Noto Sans SC", "黑体", weight, "")
	case "NotoSerifSC":
		rememberFace("Noto Serif SC", "宋体", weight, "")
	case "NotoSansJP", "NotoSansKR", "NotoSansTC", "NotoSansHK",
		"NotoSerifJP", "NotoSerifKR", "NotoSerifTC", "NotoSerifHK":
		return
	default:
		if strings.Contains(family, "CJK") {
			return
		}
		rememberFace(family, label, weight, file)
	}
}

// fontLabel reads the font's own family name, preferring Simplified Chinese.
func fontLabel(data []byte) string {
	if len(data) < 12 {
		return ""
	}
	magic := string(data[:4])
	if magic != "\x00\x01\x00\x00" && magic != "OTTO" {
		return ""
	}
	count := int(binary.BigEndian.Uint16(data[4:6]))
	if count <= 0 || 12+count*16 > len(data) {
		return ""
	}
	nameOff, nameLen := 0, 0
	for i := 0; i < count; i++ {
		rec := 12 + i*16
		tag := string(data[rec : rec+4])
		off := int(binary.BigEndian.Uint32(data[rec+8 : rec+12]))
		length := int(binary.BigEndian.Uint32(data[rec+12 : rec+16]))
		if tag == "name" {
			nameOff, nameLen = off, length
		}
	}
	if nameOff <= 0 || nameLen < 6 || nameOff+nameLen > len(data) {
		return ""
	}
	table := data[nameOff : nameOff+nameLen]
	records := int(binary.BigEndian.Uint16(table[2:4]))
	storage := int(binary.BigEndian.Uint16(table[4:6]))
	bestScore := -1
	best := ""
	for i := 0; i < records; i++ {
		rec := 6 + i*12
		if rec+12 > len(table) {
			break
		}
		platform := binary.BigEndian.Uint16(table[rec : rec+2])
		language := binary.BigEndian.Uint16(table[rec+4 : rec+6])
		nameID := int(binary.BigEndian.Uint16(table[rec+6 : rec+8]))
		length := int(binary.BigEndian.Uint16(table[rec+8 : rec+10]))
		offset := int(binary.BigEndian.Uint16(table[rec+10 : rec+12]))
		if nameID != 1 && nameID != 16 {
			continue
		}
		start := storage + offset
		if start < 0 || start+length > len(table) {
			continue
		}
		raw := table[start : start+length]
		text := ""
		score := 0
		if platform == 0 || platform == 3 {
			if len(raw)%2 != 0 {
				continue
			}
			units := make([]uint16, len(raw)/2)
			for j := range units {
				units[j] = binary.BigEndian.Uint16(raw[j*2:])
			}
			text = string(utf16.Decode(units))
			switch language {
			case 0x0804:
				score = 4
			case 1033:
				score = 2
			default:
				score = 1
			}
		} else {
			text = string(raw)
		}
		text = strings.TrimSpace(strings.ReplaceAll(text, "\x00", ""))
		if text == "" {
			continue
		}
		if nameID == 16 {
			score++
		}
		if score > bestScore {
			bestScore = score
			best = text
		}
	}
	return best
}

// faceCovering builds a face for r at the given size, falling back across the
// registry when the preferred family has no glyph. Returns nil when nothing
// covers the rune, which is the caller's signal to draw a visible marker rather
// than silently skip it.
func faceCovering(r rune, preferFamily string, weight int, sizePx float64, embedded *opentype.Font) font.Face {
	if embedded != nil && coversRune(embedded, r) {
		if f := lookupFont(preferFamily, weight); f != nil && coversRune(f, r) {
			face, err := opentype.NewFace(f, &opentype.FaceOptions{Size: sizePx, DPI: 72, Hinting: font.HintingFull})
			if err == nil {
				return face
			}
		}
		face, err := opentype.NewFace(embedded, &opentype.FaceOptions{Size: sizePx, DPI: 72, Hinting: font.HintingFull})
		if err == nil {
			return face
		}
	}
	f := fontCovering(r, preferFamily, weight)
	if f == nil {
		return nil
	}
	face, err := opentype.NewFace(f, &opentype.FaceOptions{Size: sizePx, DPI: 72, Hinting: font.HintingFull})
	if err != nil {
		return nil
	}
	return face
}

// Missing-glyph reporting. A design whose text exports blank is nearly
// impossible to diagnose from the outside, so the first time each script is
// dropped the renderer says so once, rather than per glyph per page.
var (
	missingMu   sync.Mutex
	missingSeen = map[rune]bool{}
	missingLog  func(r rune)
)

// SetMissingGlyphReporter installs a callback invoked once per uncovered rune.
// The server wires this to its logger at startup.
func SetMissingGlyphReporter(fn func(r rune)) {
	missingMu.Lock()
	defer missingMu.Unlock()
	missingLog = fn
}

func noteMissingGlyph(r rune) {
	missingMu.Lock()
	defer missingMu.Unlock()
	if missingSeen[r] || missingLog == nil {
		return
	}
	missingSeen[r] = true
	missingLog(r)
}
