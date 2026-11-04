package persistence

import (
	"context"
)

// BackfillContentSwarmCoverDocKinds scans recent designs whose doc_kind is not
// yet tagged and sets doc_kind for files that match ContentSwarm cover automation
// markers. Safe to run on every boot; already-tagged rows are skipped by the
// query.
func (s *Service) BackfillContentSwarmCoverDocKinds(ctx context.Context, limit int) (int, error) {
	if s.storage == nil || limit <= 0 {
		return 0, nil
	}
	const q = `SELECT id, "workspace_id" FROM "designs"
		WHERE "deleted_at" IS NULL AND COALESCE("doc_kind", '') <> $1
		ORDER BY "updated_at" DESC LIMIT $2`
	rows, err := s.db.Query(ctx, q, DocKindContentSwarmCover, limit)
	if err != nil {
		return 0, err
	}
	defer rows.Close()

	type candidate struct {
		id, workspaceID string
	}
	candidates := []candidate{}
	for rows.Next() {
		var id, workspaceID string
		if err := rows.Scan(&id, &workspaceID); err != nil {
			return 0, err
		}
		candidates = append(candidates, candidate{id: id, workspaceID: workspaceID})
	}
	if err := rows.Err(); err != nil {
		return 0, err
	}

	updated := 0
	for _, c := range candidates {
		loaded, err := s.LoadFile(ctx, c.id, c.workspaceID)
		if err != nil || !FileMarksContentSwarmCover(loaded.File) {
			continue
		}
		if _, err := s.db.Exec(ctx, `UPDATE "designs" SET "doc_kind" = $1 WHERE id = $2`, DocKindContentSwarmCover, c.id); err != nil {
			return updated, err
		}
		updated++
	}
	return updated, nil
}
