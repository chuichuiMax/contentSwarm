// The editor route. The editor owns a live canvas and browser-only APIs, so it
// is loaded client-side only (no SSR), which is compatible with static export.

import Head from "next/head";
import { RequireAuth } from "@/components/RequireAuth";
import { EditorApp } from "@/components/editor/EditorApp";
import { tr } from "@/lib/i18n";

export default function EditorPage() {
  return (
    <>
      <Head>
        <title>{tr("page.hycanvas_editor")}</title>
      </Head>
      <RequireAuth>
        <EditorApp />
      </RequireAuth>
    </>
  );
}
