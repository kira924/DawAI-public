import { ShieldAlert } from "lucide-react";

import { useAuth } from "../auth/useAuth";
import { BrandMark } from "../components/BrandMark";
import { LoadingScreen } from "../components/LoadingScreen";
import { LoginPage } from "../features/auth/LoginPage";
import { useI18n } from "../i18n/useI18n";
import { Workspace } from "./Workspace";

export function App() {
  const { state, user, logout } = useAuth();
  const { t } = useI18n();

  if (state === "booting") return <LoadingScreen />;
  if (state === "anonymous" || !user) return <LoginPage />;

  if (user.role === "super_admin") {
    return (
      <main className="access-page">
        <BrandMark />
        <span className="access-icon"><ShieldAlert size={30} /></span>
        <h1>{t("accessUnavailable")}</h1>
        <p>{t("accessUnavailableBody")}</p>
        <button className="button button-secondary" type="button" onClick={() => void logout()}>{t("logout")}</button>
      </main>
    );
  }

  return <Workspace user={user} />;
}
