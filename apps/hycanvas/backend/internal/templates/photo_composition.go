package templates

import (
	"bytes"
	"encoding/base64"
	"fmt"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"math"
)

type PhotoCell struct {
	Row     int `json:"row"`
	Col     int `json:"col"`
	RowSpan int `json:"rowSpan"`
	ColSpan int `json:"colSpan"`
}
type CompositionImage struct {
	InstantiateImage
	FocalX float64 `json:"focalX"`
	FocalY float64 `json:"focalY"`
}
type PhotoComposition struct {
	Rows   int                `json:"rows"`
	Cols   int                `json:"cols"`
	Gap    float64            `json:"gap"`
	Cells  []PhotoCell        `json:"cells"`
	Images []CompositionImage `json:"images"`
}

// The photo grid is an ordinary editable grid/frame/image tree, identical to
// editor insertion. Preview and persisted designs use this same transformation.
func applyPhotoComposition(file map[string]any, in *PhotoComposition) error {
	if in.Rows < 1 || in.Rows > 3 || in.Cols < 1 || in.Cols > 3 || len(in.Cells) < 2 || len(in.Cells) > 9 || len(in.Images) != len(in.Cells) || in.Gap < 0 || in.Gap > 100 {
		return ErrBadRequest
	}
	occupied := make(map[int]bool)
	assets := make([]string, len(in.Images))
	sizes := make([]image.Config, len(in.Images))
	for i, cell := range in.Cells {
		if cell.Row < 0 || cell.Col < 0 || cell.RowSpan < 1 || cell.ColSpan < 1 || cell.Row+cell.RowSpan > in.Rows || cell.Col+cell.ColSpan > in.Cols {
			return ErrBadRequest
		}
		for r := cell.Row; r < cell.Row+cell.RowSpan; r++ {
			for c := cell.Col; c < cell.Col+cell.ColSpan; c++ {
				key := r*in.Cols + c
				if occupied[key] {
					return ErrBadRequest
				}
				occupied[key] = true
			}
		}
		img := in.Images[i]
		if math.IsNaN(img.FocalX) || math.IsNaN(img.FocalY) || img.FocalX < 0 || img.FocalX > 1 || img.FocalY < 0 || img.FocalY > 1 {
			return ErrBadRequest
		}
		data, err := base64.StdEncoding.DecodeString(img.DataBase64)
		if err != nil {
			return ErrBadRequest
		}
		cfg, _, err := image.DecodeConfig(bytes.NewReader(data))
		if err != nil {
			return ErrBadRequest
		}
		sizes[i] = cfg
		assets[i] = registerInlineImageAsset(file, img.InstantiateImage)
	}
	if len(occupied) != in.Rows*in.Cols {
		return ErrBadRequest
	}
	for pageIndex, raw := range asArr(file["pages"]) {
		page := asObj(raw)
		w, h := asNum(page["width"]), asNum(page["height"])
		cw, ch := (w-in.Gap*float64(in.Cols-1))/float64(in.Cols), (h-in.Gap*float64(in.Rows-1))/float64(in.Rows)
		if cw <= 0 || ch <= 0 {
			return ErrBadRequest
		}
		gridID := fmt.Sprintf("contentswarm-composition-%d", pageIndex)
		children, cells := []any{}, []any{}
		for i, cell := range in.Cells {
			id := fmt.Sprintf("%s-cell-%d", gridID, i)
			width, height := cw*float64(cell.ColSpan)+in.Gap*float64(cell.ColSpan-1), ch*float64(cell.RowSpan)+in.Gap*float64(cell.RowSpan-1)
			photo := compositionNode(id+"-image", "image", 0, 0, width, height)
			photo["source"] = map[string]any{"assetId": assets[i], "naturalWidth": float64(sizes[i].Width), "naturalHeight": float64(sizes[i].Height)}
			photo["fit"] = "cover"
			photo["focalPoint"] = map[string]any{"x": in.Images[i].FocalX, "y": in.Images[i].FocalY}
			frame := compositionNode(id, "frame", float64(cell.Col)*(cw+in.Gap), float64(cell.Row)*(ch+in.Gap), width, height)
			frame["clip"], frame["maskShape"], frame["fills"], frame["children"] = true, "rect", []any{}, []any{photo}
			children = append(children, frame)
			cells = append(cells, map[string]any{"row": cell.Row, "col": cell.Col, "rowSpan": cell.RowSpan, "colSpan": cell.ColSpan, "childId": id})
		}
		grid := compositionNode(gridID, "grid", 0, 0, w, h)
		grid["rows"], grid["cols"], grid["gap"], grid["cells"], grid["children"] = in.Rows, in.Cols, in.Gap, cells, children
		grid["name"] = "图库图片组合"
		page["children"] = append([]any{grid}, asArr(page["children"])...)
		delete(page, "background")
	}
	return nil
}
func compositionNode(id, kind string, x, y, w, h float64) map[string]any {
	return map[string]any{"id": id, "type": kind, "name": "组合图片", "transform": map[string]any{"x": x, "y": y, "scaleX": 1.0, "scaleY": 1.0, "rotation": 0.0}, "size": map[string]any{"width": w, "height": h}, "opacity": 1.0, "blendMode": "normal", "locked": false}
}
