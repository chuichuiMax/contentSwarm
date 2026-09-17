import Head from "next/head";
import dynamic from "next/dynamic";
import { RequireAuth } from "@/components/RequireAuth";
import { dashboardTitles, type DashboardView } from "./views";
import { tr } from "@/lib/i18n";

const DashboardApp = dynamic(() => import("./DashboardApp").then((module) => module.DashboardApp), {
  ssr: false,
  loading: () => <div className="grid min-h-screen place-items-center text-sm text-neutral-500">{tr("page.loading")}</div>,
});

export function DashboardRoutePage({ view }: { view: DashboardView }) {
  return (
    <>
      <Head><title>{`${dashboardTitles[view]} · HyCanvas`}</title></Head>
      <RequireAuth><DashboardApp view={view} /></RequireAuth>
    </>
  );
}
