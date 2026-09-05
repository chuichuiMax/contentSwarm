import Head from "next/head";
import { useEffect } from "react";
import { AuthForm } from "@/components/auth/AuthForm";
import { FullScreenLoader } from "@/components/ui/BrandLoader";
import { tr } from "@/lib/i18n";
import { isContentSwarmManaged, returnToContentSwarm } from "@/lib/managedAuth";

export default function LoginPage() {
  useEffect(() => {
    if (isContentSwarmManaged) returnToContentSwarm();
  }, []);

  return (
    <>
      <Head>
        <title>{tr("page.sign_in_hycanvas")}</title>
      </Head>
      {isContentSwarmManaged ? <FullScreenLoader label="正在返回 ContentSwarm" /> : <AuthForm mode="login" />}
    </>
  );
}
