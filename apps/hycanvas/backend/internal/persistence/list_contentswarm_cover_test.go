package persistence

import (
	"context"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"

	"hycanvas/backend/internal/storage"
)

func TestListByWorkspaceExcludesContentSwarmCovers(t *testing.T) {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		t.Skip("DATABASE_URL not set; skipping DB integration test")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, stripSchema(dsn))
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	defer pool.Close()

	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatalf("begin: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	ownerID := uuid.NewString()
	if _, err = tx.Exec(ctx,
		`INSERT INTO "users" (id, email, name, "updated_at") VALUES ($1,$2,'Owner', now())`,
		ownerID, "cover-list+"+uuid.NewString()+"@example.com",
	); err != nil {
		t.Fatalf("user: %v", err)
	}
	wsID := uuid.NewString()
	if _, err = tx.Exec(ctx,
		`INSERT INTO "workspaces" (id, kind, name, slug, "owner_id", "updated_at") VALUES ($1,'PERSONAL','W',$2,$3, now())`,
		wsID, "ws-"+uuid.NewString()[:8], ownerID,
	); err != nil {
		t.Fatalf("workspace: %v", err)
	}

	plainID := uuid.NewString()
	coverID := uuid.NewString()
	for _, row := range []struct{ id, kind string }{
		{plainID, "design"},
		{coverID, DocKindContentSwarmCover},
	} {
		_, err = tx.Exec(ctx,
			`INSERT INTO "designs" (id, "workspace_id", title, "doc_kind", "updated_at") VALUES ($1,$2,$3,$4, now())`,
			row.id, wsID, row.id, row.kind,
		)
		if err != nil {
			t.Fatalf("design insert: %v", err)
		}
	}

	svc := NewService(tx)
	list, err := svc.ListByWorkspace(ctx, wsID, 50)
	if err != nil {
		t.Fatalf("ListByWorkspace: %v", err)
	}
	if len(list) != 1 || list[0].ID != plainID {
		t.Fatalf("list = %+v, want only %s", list, plainID)
	}
}

func TestBackfillContentSwarmCoverDocKinds(t *testing.T) {
	dsn := os.Getenv("DATABASE_URL")
	if dsn == "" {
		t.Skip("DATABASE_URL not set; skipping DB integration test")
	}
	ctx := context.Background()
	pool, err := pgxpool.New(ctx, stripSchema(dsn))
	if err != nil {
		t.Fatalf("pool: %v", err)
	}
	defer pool.Close()

	tx, err := pool.Begin(ctx)
	if err != nil {
		t.Fatalf("begin: %v", err)
	}
	defer func() { _ = tx.Rollback(ctx) }()

	ownerID := uuid.NewString()
	if _, err = tx.Exec(ctx,
		`INSERT INTO "users" (id, email, name, "updated_at") VALUES ($1,$2,'Owner', now())`,
		ownerID, "cover-backfill+"+uuid.NewString()+"@example.com",
	); err != nil {
		t.Fatalf("user: %v", err)
	}
	wsID := uuid.NewString()
	if _, err = tx.Exec(ctx,
		`INSERT INTO "workspaces" (id, kind, name, slug, "owner_id", "updated_at") VALUES ($1,'PERSONAL','W',$2,$3, now())`,
		wsID, "ws-"+uuid.NewString()[:8], ownerID,
	); err != nil {
		t.Fatalf("workspace: %v", err)
	}

	store, err := storage.NewLocal(t.TempDir())
	if err != nil {
		t.Fatalf("storage: %v", err)
	}
	svc := NewService(tx).WithStorage(store)

	designID := uuid.NewString()
	file := DesignFile{
		"id":            designID,
		"title":         "Cover",
		"schemaVersion": CurrentSchemaVersion,
		"pages": []any{
			map[string]any{
				"id":     "page-1",
				"width":  1080,
				"height": 1440,
				"children": []any{
					map[string]any{"id": "contentswarm-background-0", "type": "image"},
				},
			},
		},
	}
	if _, err := svc.Create(ctx, wsID, "Cover", file, nil); err != nil {
		t.Fatalf("Create: %v", err)
	}
	_, err = tx.Exec(ctx, `UPDATE "designs" SET "doc_kind" = NULL WHERE id = $1`, designID)
	if err != nil {
		t.Fatalf("clear doc_kind: %v", err)
	}

	n, err := svc.BackfillContentSwarmCoverDocKinds(ctx, 100)
	if err != nil {
		t.Fatalf("Backfill: %v", err)
	}
	if n != 1 {
		t.Fatalf("updated = %d, want 1", n)
	}
	list, err := svc.ListByWorkspace(ctx, wsID, 50)
	if err != nil {
		t.Fatalf("ListByWorkspace: %v", err)
	}
	if len(list) != 0 {
		t.Fatalf("cover should be hidden after backfill: %+v", list)
	}
}
