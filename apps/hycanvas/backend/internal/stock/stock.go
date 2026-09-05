// Package stock ports the NestJS stock module (doc 13): a global, read-only
// stock catalog (embedded seed of 42 assets + 11 collections), search, per-user
// favorites + recents, and an allowlisted image proxy (SSRF-guarded) so the
// editor places stock from our own origin. Search is a pure port of @hc/stock.
// Insertion to native nodes happens client-side. Assets are opaque JSON.
package stock

import (
	"context"
	"embed"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"

	"hycanvas/backend/internal/media"
)

//go:embed stock.json
var stockJSON []byte

// The bundled open-licensed asset library (ingested by scripts/ingest-stock.mjs):
// per-pack dirs of raw SVG files plus an index.json with asset metadata, the
// pack license, and a collection entry. Embedded so the single self-host binary
// ships the full library.
//
//go:embed library
var libraryFS embed.FS

// Errors map to RFC 7807 statuses at the HTTP layer.
var (
	ErrNotFound   = errors.New("not found")
	ErrBadRequest = errors.New("bad request")
)

// proxyAllowlist are the hosts the image proxy may fetch from (the seed sources).
var proxyAllowlist = map[string]bool{"picsum.photos": true, "fastly.picsum.photos": true}

const recentsCap = 30

type seedData struct {
	Stock       []map[string]any `json:"stock"`
	Collections []any            `json:"collections"`
}

var seed = func() seedData {
	var d seedData
	_ = json.Unmarshal(stockJSON, &d)
	localizeSeedPhotos(&d)
	loadLibrary(&d)
	return d
}()

// localizeSeedPhotos replaces the original Lorem Picsum demo URLs with small,
// deterministic illustrations embedded directly in the catalog response. The
// starter catalog must remain usable offline: previews and canvas insertion no
// longer depend on a third-party placeholder service, while ids, categories,
// favorites and recents remain stable for existing installations.
func localizeSeedPhotos(d *seedData) {
	for _, asset := range d.Stock {
		id := str(asset["id"])
		svg := bundledPhotoSVG(id)
		if svg == nil || str(asset["kind"]) != "photo" {
			continue
		}
		dataURL := "data:image/svg+xml;base64," + base64.StdEncoding.EncodeToString(svg)
		asset["previewUrl"] = dataURL
		asset["sourceUrl"] = dataURL
		asset["format"] = "svg"
	}
}

var bundledPhotoArt = map[string][3]string{
	"mountain":  {"#ffb36b", "#5667a8", `<circle cx="1250" cy="250" r="130" fill="#fff2c7"/><path d="M0 1030 470 420 760 760 1010 360 1600 1030Z" fill="#263b63"/><path d="m370 550 100-130 110 142-95-38Z" fill="#fff" opacity=".8"/>`},
	"forest":    {"#d6e6d4", "#466b55", `<g fill="#214a36"><path d="m180 980 180-590 180 590Z"/><path d="m520 1030 220-720 220 720Z"/><path d="m900 990 185-610 185 610Z"/><path d="m1210 1030 150-500 150 500Z"/></g><rect y="930" width="1600" height="270" fill="#163529"/>`},
	"city":      {"#8fc6df", "#f0bfa3", `<g fill="#27364a"><rect x="100" y="520" width="260" height="680"/><rect x="400" y="350" width="300" height="850"/><rect x="750" y="600" width="250" height="600"/><rect x="1040" y="260" width="390" height="940"/></g><g fill="#f9d878" opacity=".9"><path d="M150 590h55v70h-55zm100 0h55v70h-55zm210-160h65v80h-65zm110 0h65v80h-65zm540-80h70v90h-70zm120 0h70v90h-70z"/></g>`},
	"beach":     {"#82d8ed", "#2875aa", `<path d="M0 690q350-120 700 0t900 0v510H0Z" fill="#137fa7"/><path d="M0 900q420-160 800 0t800 0v300H0Z" fill="#f1d59a"/><circle cx="1280" cy="250" r="125" fill="#ffe08a"/><path d="m410 900 170-370 170 370Z" fill="#ef5b4c"/>`},
	"desert":    {"#f7cd77", "#e68948", `<circle cx="1260" cy="245" r="135" fill="#fff0ad"/><path d="M0 860q390-330 820 0t780-40v380H0Z" fill="#d87b38"/><path d="M0 1030q460-300 950-40t650-80v290H0Z" fill="#f1aa52"/>`},
	"lake":      {"#b9dbd0", "#507f8d", `<path d="m0 720 360-330 280 270 270-390 420 450 270-210v690H0Z" fill="#365b62"/><path d="M0 720h1600v480H0Z" fill="#74aeb5"/><path d="m910 720 280 360H650Z" fill="#cfe7df" opacity=".65"/>`},
	"flowers":   {"#f8e7c8", "#8fc8a5", `<g stroke="#39785a" stroke-width="18"><path d="M350 1100 410 480M770 1100 720 390M1160 1100l-40-520"/></g><g fill="#f05d77"><circle cx="410" cy="430" r="100"/><circle cx="720" cy="350" r="115"/></g><g fill="#ffc94a"><circle cx="1120" cy="540" r="120"/><circle cx="410" cy="430" r="35" fill="#7d4935"/><circle cx="720" cy="350" r="38" fill="#7d4935"/><circle cx="1120" cy="540" r="40" fill="#7d4935"/></g>`},
	"coffee":    {"#e8d6c0", "#8b6654", `<rect x="230" y="760" width="1140" height="90" rx="45" fill="#68483c"/><path d="M560 390h420v360H560q-80-170 0-360Z" fill="#f7f0e7"/><path d="M980 450h90q150 0 150 115t-150 115h-90" fill="none" stroke="#f7f0e7" stroke-width="55"/><ellipse cx="770" cy="410" rx="190" ry="65" fill="#4b2d24"/><path d="M650 300q-80-120 20-210M800 290q-70-120 30-210" fill="none" stroke="#fff" stroke-width="24" opacity=".6"/>`},
	"workspace": {"#dce3ea", "#8093aa", `<rect x="180" y="790" width="1240" height="90" rx="30" fill="#34465c"/><rect x="430" y="270" width="740" height="470" rx="30" fill="#1f2937"/><rect x="475" y="315" width="650" height="360" fill="#83c6da"/><path d="M690 740h220l70 140H620Z" fill="#52677e"/><circle cx="1320" cy="650" r="90" fill="#78a56d"/>`},
	"food":      {"#ffe3a1", "#e38d4a", `<ellipse cx="800" cy="780" rx="470" ry="260" fill="#f7f1e8"/><ellipse cx="800" cy="720" rx="390" ry="190" fill="#6d9b55"/><g fill="#ef695b"><circle cx="650" cy="690" r="70"/><circle cx="920" cy="770" r="65"/></g><g fill="#ffd85a"><circle cx="800" cy="650" r="75"/><circle cx="1040" cy="690" r="55"/></g><path d="m540 590 130 70-95 120Z" fill="#f3f0d2"/>`},
	"abstract":  {"#7656c8", "#f39c83", `<circle cx="390" cy="360" r="260" fill="#ffd65a" opacity=".85"/><rect x="690" y="170" width="510" height="510" rx="110" fill="#42c8b3" opacity=".82" transform="rotate(18 945 425)"/><path d="M180 1050q360-500 650-120t590-250" fill="none" stroke="#fff" stroke-width="95" opacity=".72"/>`},
}

var legacyPicsumIDs = map[string]string{
	"1018": "mountain", "1015": "forest", "1016": "city", "1020": "beach",
	"1024": "desert", "1019": "lake", "1025": "flowers", "1060": "coffee",
	"1071": "workspace", "1080": "food", "1062": "abstract",
}

func bundledPhotoSVG(id string) []byte {
	art, ok := bundledPhotoArt[id]
	if !ok {
		return nil
	}
	return []byte(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1600 1200">` +
		`<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop stop-color="` + art[0] + `"/><stop offset="1" stop-color="` + art[1] + `"/></linearGradient></defs>` +
		`<rect width="1600" height="1200" fill="url(#g)"/>` + art[2] + `</svg>`)
}

// loadLibrary appends every bundled library pack to the seed catalog. Each
// asset's SVG is inlined (the client inserts it as editable vectors, same as
// the seeded icons) and enriched with the pack's kind/category/license, so
// search, favorites, recents, and attribution treat packs like any other asset.
func loadLibrary(d *seedData) {
	packs, err := libraryFS.ReadDir("library")
	if err != nil {
		return // no packs bundled yet (before the first ingest run)
	}
	for _, p := range packs {
		if !p.IsDir() {
			continue
		}
		dir := "library/" + p.Name()
		raw, err := libraryFS.ReadFile(dir + "/index.json")
		if err != nil {
			continue
		}
		var idx struct {
			Pack       string           `json:"pack"`
			Kind       string           `json:"kind"`
			Category   string           `json:"category"`
			License    map[string]any   `json:"license"`
			Collection map[string]any   `json:"collection"`
			Assets     []map[string]any `json:"assets"`
		}
		if err := json.Unmarshal(raw, &idx); err != nil {
			continue
		}
		for _, a := range idx.Assets {
			file, _ := a["file"].(string)
			svg, err := libraryFS.ReadFile(dir + "/" + file)
			if err != nil {
				continue
			}
			delete(a, "file")
			a["svg"] = string(svg)
			a["kind"] = idx.Kind
			a["category"] = idx.Category
			a["license"] = idx.License
			a["pack"] = idx.Pack
			a["format"] = "svg"
			a["animated"] = false
			a["previewUrl"] = ""
			a["sourceUrl"] = ""
			a["collectionIds"] = []any{idx.Pack}
			// stockMatches expects style as an array (seed assets use one).
			if s, ok := a["style"].(string); ok {
				a["style"] = []any{s}
			}
			d.Stock = append(d.Stock, a)
		}
		if idx.Collection != nil {
			if idx.Kind != "" {
				idx.Collection["kind"] = idx.Kind
			}
			// Mark pack collections so the browse UI can separate curated themes
			// (top-level Collection chips) from bundled-pack sources, which render
			// as a per-kind Source facet instead of cluttering the theme row.
			idx.Collection["source"] = "pack"
			d.Collections = append(d.Collections, idx.Collection)
		}
	}
}

var stockByID = func() map[string]map[string]any {
	m := map[string]map[string]any{}
	for _, a := range seed.Stock {
		if id, ok := a["id"].(string); ok {
			m[id] = a
		}
	}
	return m
}()

// FacetValue is one value of a filterable facet, scoped to an asset kind so
// the client can offer only the filters that apply to what the user browses.
type FacetValue struct {
	ID    string `json:"id"`
	Kind  string `json:"kind"`
	Count int    `json:"count"`
}

// Filters are the catalog's filterable facets, aggregated from the bundled
// assets at init and sorted by count (desc) then id, ready to render as-is.
// New packs, categories, or orientations surface here with no client change.
type Filters struct {
	Categories   []FacetValue `json:"categories"`
	Styles       []FacetValue `json:"styles"`
	Orientations []FacetValue `json:"orientations"`
}

var stockFilters = func() Filters {
	type key struct{ facet, kind, id string }
	counts := map[key]int{}
	for _, a := range seed.Stock {
		kind := str(a["kind"])
		if c := str(a["category"]); c != "" {
			counts[key{"category", kind, c}]++
		}
		if o := str(a["orientation"]); o != "" {
			counts[key{"orientation", kind, o}]++
		}
		for _, v := range arr(a["style"]) {
			if sv := str(v); sv != "" {
				counts[key{"style", kind, sv}]++
			}
		}
	}
	f := Filters{Categories: []FacetValue{}, Styles: []FacetValue{}, Orientations: []FacetValue{}}
	for k, n := range counts {
		v := FacetValue{ID: k.id, Kind: k.kind, Count: n}
		switch k.facet {
		case "category":
			f.Categories = append(f.Categories, v)
		case "style":
			f.Styles = append(f.Styles, v)
		case "orientation":
			f.Orientations = append(f.Orientations, v)
		}
	}
	for _, vs := range [][]FacetValue{f.Categories, f.Styles, f.Orientations} {
		sort.Slice(vs, func(i, j int) bool {
			if vs[i].Count != vs[j].Count {
				return vs[i].Count > vs[j].Count
			}
			return vs[i].ID < vs[j].ID
		})
	}
	return f
}()

// DBTX is the query surface (satisfied by *pgxpool.Pool and pgx.Tx).
type DBTX interface {
	QueryRow(ctx context.Context, sql string, args ...any) pgx.Row
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
	Exec(ctx context.Context, sql string, args ...any) (pgconn.CommandTag, error)
}

// liveProvider is a live upstream stock source (Openverse photos, Iconify
// icons, ...). Each provider serves one or more StockKinds; search returns
// catalog-shaped assets (the same map[string]any shape as the bundled seed) or
// nil on ANY failure (disabled, network error, decode error), so the caller
// always has the seed to fall back to and a slow or broken upstream can never
// break stock search. New providers register in NewService.
type liveProvider interface {
	// handles reports whether this provider serves the given StockKind.
	handles(kind string) bool
	// search queries the upstream for q. Returns nil to fall back to the seed.
	search(ctx context.Context, q Query) []map[string]any
}

// Service is the stock module.
type Service struct {
	db     DBTX
	client *http.Client
	// live upstream providers, tried (first match by kind) for plain text
	// searches; the bundled seed backs every kind and every failure.
	live []liveProvider
}

// NewService wires the stock service and its live providers.
func NewService(db DBTX) *Service {
	return &Service{
		db:     db,
		client: &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }},
		live:   []liveProvider{newOpenverse(), newIconify()},
	}
}

// liveFor returns the first registered provider that serves kind, or nil.
func (s *Service) liveFor(kind string) liveProvider {
	for _, p := range s.live {
		if p.handles(kind) {
			return p
		}
	}
	return nil
}

// Query is the stock search query.
type Query struct {
	Text         string
	Kind         string
	Orientation  string
	Color        string
	Category     string
	Style        string
	CollectionID string
	// Paging: the bundled library is ~10k assets with inline SVG, so responses
	// are always capped. Limit <= 0 uses the default page size.
	Limit  int
	Offset int
}

const (
	defaultSearchLimit = 60
	maxSearchLimit     = 200
)

// --- search (pure port of @hc/stock) -------------------------------------

func textRelevance(a map[string]any, q string) int {
	needle := strings.ToLower(q)
	title := strings.ToLower(str(a["title"]))
	if title == needle {
		return 100
	}
	score := 0
	if strings.Contains(title, needle) {
		score += 50
	}
	for _, t := range arr(a["tags"]) {
		ts := strings.ToLower(str(t))
		if ts == needle {
			score += 20
		} else if strings.Contains(ts, needle) {
			score += 8
		}
	}
	return score
}

func colorMatches(a map[string]any, hex string) bool {
	target, ok := hexToRGB(hex)
	if !ok {
		return false
	}
	for _, c := range arr(a["dominantColors"]) {
		rgb, ok := hexToRGB(str(c))
		if !ok {
			continue
		}
		d := math.Sqrt(math.Pow(rgb[0]-target[0], 2) + math.Pow(rgb[1]-target[1], 2) + math.Pow(rgb[2]-target[2], 2))
		if d <= 70 {
			return true
		}
	}
	return false
}

// sortByColorfulness orders one kind's browse results: most colorful first,
// with a title tiebreak so the order is deterministic for offset paging.
func sortByColorfulness(assets []map[string]any) {
	score := make(map[string]float64, len(assets))
	for _, a := range assets {
		score[str(a["id"])] = colorfulness(a)
	}
	sort.SliceStable(assets, func(i, j int) bool {
		si, sj := score[str(assets[i]["id"])], score[str(assets[j]["id"])]
		if si != sj {
			return si > sj
		}
		return str(assets[i]["title"]) < str(assets[j]["title"])
	})
}

// interleaveByKind orders a mixed browse (no kind filter) by round-robin across
// kinds (photo, illustration, sticker, icon, then any other kind in id order),
// most colorful first within each kind. The first screen thus represents every
// kind instead of opening on a wall of one, and the order is deterministic so
// offset paging never overlaps.
func interleaveByKind(assets []map[string]any) []map[string]any {
	lead := []string{"photo", "illustration", "sticker", "icon"}
	known := map[string]bool{"photo": true, "illustration": true, "sticker": true, "icon": true}
	groups := map[string][]map[string]any{}
	var extra []string
	for _, a := range assets {
		k := str(a["kind"])
		if !known[k] {
			if _, seen := groups[k]; !seen {
				extra = append(extra, k)
			}
		}
		groups[k] = append(groups[k], a)
	}
	sort.Strings(extra)
	kinds := append(append([]string{}, lead...), extra...)
	buckets := make([][]map[string]any, 0, len(kinds))
	for _, k := range kinds {
		if g := groups[k]; len(g) > 0 {
			sortByColorfulness(g)
			buckets = append(buckets, g)
		}
	}
	return roundRobin(buckets)
}

// roundRobin merges pre-ordered buckets by taking one from each in turn (bucket
// 0's first, bucket 1's first, ... then bucket 0's second, ...), preserving each
// bucket's internal order. Deterministic given deterministic buckets, so offset
// paging over the result never overlaps.
func roundRobin(buckets [][]map[string]any) []map[string]any {
	total := 0
	for _, b := range buckets {
		total += len(b)
	}
	out := make([]map[string]any, 0, total)
	for round := 0; ; round++ {
		progressed := false
		for _, b := range buckets {
			if round < len(b) {
				out = append(out, b[round])
				progressed = true
			}
		}
		if !progressed {
			break
		}
	}
	return out
}

// colorfulness scores an asset's dominant palette: the widest channel spread
// (chroma) across the dominant colors, plus a small bonus per extra vivid
// color. Monochrome assets score 0.
func colorfulness(a map[string]any) float64 {
	best, vivid := 0.0, 0
	for _, c := range arr(a["dominantColors"]) {
		rgb, ok := hexToRGB(str(c))
		if !ok {
			continue
		}
		mx := math.Max(rgb[0], math.Max(rgb[1], rgb[2]))
		mn := math.Min(rgb[0], math.Min(rgb[1], rgb[2]))
		chroma := (mx - mn) / 255
		if chroma > best {
			best = chroma
		}
		if chroma >= 0.25 {
			vivid++
		}
	}
	if vivid > 4 {
		vivid = 4
	}
	return best + 0.05*float64(vivid)
}

func stockMatches(a map[string]any, q Query) bool {
	if q.Text != "" && textRelevance(a, q.Text) == 0 {
		return false
	}
	if q.Kind != "" && str(a["kind"]) != q.Kind {
		return false
	}
	if q.Orientation != "" && str(a["orientation"]) != q.Orientation {
		return false
	}
	if q.Category != "" && str(a["category"]) != q.Category {
		return false
	}
	if q.Color != "" && !colorMatches(a, q.Color) {
		return false
	}
	if q.Style != "" {
		ok := false
		for _, s := range arr(a["style"]) {
			if str(s) == q.Style {
				ok = true
				break
			}
		}
		if !ok {
			return false
		}
	}
	if q.CollectionID != "" {
		ok := false
		for _, c := range arr(a["collectionIds"]) {
			if str(c) == q.CollectionID {
				ok = true
				break
			}
		}
		if !ok {
			return false
		}
	}
	return true
}

func searchStock(assets []map[string]any, q Query) []map[string]any {
	var matched []map[string]any
	for _, a := range assets {
		if stockMatches(a, q) {
			matched = append(matched, a)
		}
	}
	if q.Text != "" {
		sort.SliceStable(matched, func(i, j int) bool {
			si, sj := textRelevance(matched[i], q.Text), textRelevance(matched[j], q.Text)
			if si != sj {
				return si > sj
			}
			return str(matched[i]["title"]) < str(matched[j]["title"])
		})
	} else if q.Kind == "" {
		// Mixed browse (no kind filter, no text): interleave kinds round-robin so
		// the first screen represents photos, illustrations, and icons instead of
		// opening on a single kind. Deterministic so offset paging stays stable.
		matched = interleaveByKind(matched)
	} else {
		// Single-kind browse: most colorful first, deterministic title tiebreak.
		sortByColorfulness(matched)
	}
	// Page the ranked results (see Query.Limit): an unbounded response over the
	// bundled library would ship megabytes of inline SVG per request.
	limit := q.Limit
	if limit <= 0 {
		limit = defaultSearchLimit
	}
	if limit > maxSearchLimit {
		limit = maxSearchLimit
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}
	if offset >= len(matched) {
		return nil
	}
	if end := offset + limit; end < len(matched) {
		return matched[offset:end]
	}
	return matched[offset:]
}

// --- public API ----------------------------------------------------------

// Search returns matching catalog assets, each flagged with the user's favorite.
// Text photo searches go to the live Openverse provider (open-licensed, results
// commercially safe); everything else, and any provider failure, is served from
// the bundled catalog. Facet filters (category, style, orientation, color) are
// bundled-only: the provider can't apply them, so an active facet keeps the
// search local rather than returning results that silently ignore it.
func (s *Service) Search(ctx context.Context, q Query, userID string) ([]map[string]any, error) {
	// A plain text search (no bundled-only facet like collection/category/style/
	// orientation/color) for a kind a live provider serves goes upstream first;
	// the bundled seed is the fallback for everything else and every failure.
	// Facets stay seed-only because upstream providers can't apply them.
	if strings.TrimSpace(q.Text) != "" && q.CollectionID == "" &&
		q.Category == "" && q.Style == "" && q.Orientation == "" && q.Color == "" {
		// "All" (no kind) merges every kind so a default search spans live photos
		// and icons plus the bundled illustrations/emoji, not just the bundle.
		if q.Kind == "" {
			return s.searchAllMerged(ctx, q, userID)
		}
		if p := s.liveFor(q.Kind); p != nil {
			if live := p.search(ctx, q); live != nil {
				return s.withFlags(ctx, live, userID)
			}
		}
	}
	return s.withFlags(ctx, searchStock(seed.Stock, q), userID)
}

// searchAllMerged serves an "All" (no kind) text search by querying every
// searchable kind in parallel — the live provider where one serves the kind
// (photos via Openverse, icons via Iconify), the bundled catalog otherwise
// (illustrations, emoji, and any kind whose live provider fails) — then
// interleaving the kinds so the results span all of them instead of opening on
// one. Paging is over the merged, kind-interleaved order; each kind's bucket is
// fetched for the whole window (offset+limit) so the slice is stable.
func (s *Service) searchAllMerged(ctx context.Context, q Query, userID string) ([]map[string]any, error) {
	limit := q.Limit
	if limit <= 0 {
		limit = defaultSearchLimit
	}
	if limit > maxSearchLimit {
		limit = maxSearchLimit
	}
	offset := q.Offset
	if offset < 0 {
		offset = 0
	}
	need := offset + limit

	// Fetch each kind's bucket concurrently; a fixed slot per kind keeps the
	// merged order deterministic regardless of which fetch finishes first.
	lead := []string{"photo", "illustration", "sticker", "icon"}
	slots := make([][]map[string]any, len(lead))
	var wg sync.WaitGroup
	for i, k := range lead {
		i, k := i, k
		wg.Add(1)
		go func() {
			defer wg.Done()
			p := s.liveFor(k)
			var bucket []map[string]any
			if p != nil {
				bucket = p.search(ctx, Query{Text: q.Text, Kind: k, Limit: need})
			}
			// Fall back to the bundled catalog when this kind has no live provider,
			// or (only on the first page) when the live provider failed. On deeper
			// pages a live kind stays live-or-empty: swapping in a differently
			// sized/ordered bundled bucket mid-pagination would re-interleave the
			// merged order and resurface or skip already-shown tiles.
			if bucket == nil && (p == nil || offset == 0) {
				bucket = searchStock(seed.Stock, Query{Text: q.Text, Kind: k, Limit: need})
			}
			slots[i] = bucket
		}()
	}
	wg.Wait()

	buckets := make([][]map[string]any, 0, len(slots))
	for _, b := range slots {
		if len(b) > 0 {
			buckets = append(buckets, b)
		}
	}
	merged := roundRobin(buckets)
	if offset >= len(merged) {
		return s.withFlags(ctx, nil, userID)
	}
	end := offset + limit
	if end > len(merged) {
		end = len(merged)
	}
	return s.withFlags(ctx, merged[offset:end], userID)
}

// Get returns one catalog asset.
func (s *Service) Get(id string) (map[string]any, error) {
	a, ok := stockByID[id]
	if !ok {
		return nil, ErrNotFound
	}
	return a, nil
}

// Collections returns the curated catalog collections.
func (s *Service) Collections() []any { return seed.Collections }

// Filters returns the filterable facets of the bundled catalog.
func (s *Service) Filters() Filters { return stockFilters }

// ToggleFavorite flips the user's favorite on a stock asset.
func (s *Service) ToggleFavorite(ctx context.Context, userID, stockID string) (bool, error) {
	if _, ok := stockByID[stockID]; !ok {
		return false, ErrNotFound
	}
	isFav, err := s.isFavorite(ctx, userID, stockID)
	if err != nil {
		return false, err
	}
	if isFav {
		return false, s.removeFavorite(ctx, userID, stockID)
	}
	return true, s.addFavorite(ctx, userID, stockID)
}

// ListFavorites returns the user's favorited assets (newest first).
func (s *Service) ListFavorites(ctx context.Context, userID string) ([]map[string]any, error) {
	ids, err := s.favoriteIDs(ctx, userID)
	if err != nil {
		return nil, err
	}
	out := []map[string]any{}
	for _, id := range ids {
		if a, ok := stockByID[id]; ok {
			out = append(out, withFlag(a, true))
		}
	}
	return out, nil
}

// RecordRecent records a stock asset as recently used (on insertion).
func (s *Service) RecordRecent(ctx context.Context, userID, stockID string) error {
	if _, ok := stockByID[stockID]; !ok {
		return ErrNotFound
	}
	return s.recordRecent(ctx, userID, stockID, recentsCap)
}

// ListRecents returns the user's recently-used assets (most recent first).
func (s *Service) ListRecents(ctx context.Context, userID string) ([]map[string]any, error) {
	ids, err := s.recentIDs(ctx, userID)
	if err != nil {
		return nil, err
	}
	var assets []map[string]any
	for _, id := range ids {
		if a, ok := stockByID[id]; ok {
			assets = append(assets, a)
		}
	}
	return s.withFlags(ctx, assets, userID)
}

func (s *Service) withFlags(ctx context.Context, assets []map[string]any, userID string) ([]map[string]any, error) {
	out := make([]map[string]any, 0, len(assets))
	if userID == "" {
		for _, a := range assets {
			out = append(out, withFlag(a, false))
		}
		return out, nil
	}
	ids, err := s.favoriteIDs(ctx, userID)
	if err != nil {
		return nil, err
	}
	fav := map[string]bool{}
	for _, id := range ids {
		fav[id] = true
	}
	for _, a := range assets {
		out = append(out, withFlag(a, fav[str(a["id"])]))
	}
	return out, nil
}

func withFlag(a map[string]any, favorited bool) map[string]any {
	out := make(map[string]any, len(a)+1)
	for k, v := range a {
		out[k] = v
	}
	out["favorited"] = favorited
	return out
}

// Proxy fetches an allowlisted stock image server-side (SSRF-guarded, redirects
// re-validated each hop) and returns its bytes + mime.
func (s *Service) Proxy(rawURL string) ([]byte, string, error) {
	const maxBytes = 15 * 1024 * 1024
	if parsed, err := url.Parse(rawURL); err == nil && (parsed.Host == "picsum.photos" || parsed.Host == "fastly.picsum.photos") {
		parts := strings.Split(strings.Trim(parsed.Path, "/"), "/")
		for i := 0; i+1 < len(parts); i++ {
			if parts[i] == "id" {
				if svg := bundledPhotoSVG(legacyPicsumIDs[parts[i+1]]); svg != nil {
					return svg, "image/svg+xml", nil
				}
				break
			}
		}
	}
	u := rawURL
	for hop := 0; hop < 4; hop++ {
		if !s.proxyURLAllowed(u) {
			return nil, "", ErrBadRequest
		}
		res, err := s.client.Get(u)
		if err != nil {
			return nil, "", ErrNotFound
		}
		if res.StatusCode >= 300 && res.StatusCode < 400 {
			loc := res.Header.Get("location")
			res.Body.Close()
			if loc == "" {
				return nil, "", ErrNotFound
			}
			next, err := resolveRef(u, loc)
			if err != nil {
				return nil, "", ErrBadRequest
			}
			u = next
			continue
		}
		defer res.Body.Close()
		if res.StatusCode < 200 || res.StatusCode >= 300 {
			return nil, "", ErrNotFound
		}
		mime := res.Header.Get("content-type")
		if mime == "" {
			mime = "image/jpeg"
		}
		if !strings.HasPrefix(mime, "image/") {
			return nil, "", ErrBadRequest
		}
		if cl, err := strconv.ParseInt(res.Header.Get("content-length"), 10, 64); err == nil && cl > maxBytes {
			return nil, "", ErrBadRequest
		}
		bytes, err := io.ReadAll(io.LimitReader(res.Body, maxBytes+1))
		if err != nil || int64(len(bytes)) > maxBytes {
			return nil, "", ErrBadRequest
		}
		return bytes, mime, nil
	}
	return nil, "", ErrBadRequest
}

// proxyURLAllowed enforces https + an allowlisted, non-private host.
func (s *Service) proxyURLAllowed(raw string) bool {
	p := media.ParseURL(raw)
	if p == nil || p.Scheme != "https" || p.Host == "" {
		return false
	}
	if media.IsPrivateIP(p.Host) {
		return false
	}
	return proxyAllowlist[p.Host]
}

func resolveRef(base, ref string) (string, error) {
	b, err := url.Parse(base)
	if err != nil {
		return "", err
	}
	r, err := url.Parse(ref)
	if err != nil {
		return "", err
	}
	return b.ResolveReference(r).String(), nil
}

// --- tiny accessors ------------------------------------------------------

func str(v any) string { s, _ := v.(string); return s }
func arr(v any) []any  { a, _ := v.([]any); return a }

func hexToRGB(hex string) ([3]float64, bool) {
	h := strings.TrimPrefix(hex, "#")
	if len(h) < 6 {
		return [3]float64{}, false
	}
	parse := func(s string) (float64, bool) {
		n, err := strconv.ParseInt(s, 16, 0)
		if err != nil {
			return 0, false
		}
		return float64(n), true
	}
	r, ok1 := parse(h[0:2])
	g, ok2 := parse(h[2:4])
	b, ok3 := parse(h[4:6])
	if !ok1 || !ok2 || !ok3 {
		return [3]float64{}, false
	}
	return [3]float64{r, g, b}, true
}
