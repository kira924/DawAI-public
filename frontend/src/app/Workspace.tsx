import {
  BarChart3,
  ChevronDown,
  Languages,
  LogOut,
  PanelLeftClose,
  PanelRightClose,
  PackagePlus,
  Pill,
  Power,
  ShoppingCart,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, normalizeApiError } from "../api/client";
import type { Shift, UserProfile } from "../api/types";
import { useAuth } from "../auth/useAuth";
import { BrandMark } from "../components/BrandMark";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { CatalogOnboardingPage } from "../features/catalog/CatalogOnboardingPage";
import { PosPage } from "../features/pos/PosPage";
import { ShiftClosedDialog } from "../features/shifts/ShiftClosedDialog";
import { ShiftDialog } from "../features/shifts/ShiftDialog";
import { useOnlineStatus } from "../hooks/useOnlineStatus";
import { useI18n } from "../i18n/useI18n";

type WorkspaceView = "pos" | "catalog" | "dashboard";

export function Workspace({ user }: { user: UserProfile }) {
  const { request, logout } = useAuth();
  const { locale, t, toggleLocale } = useI18n();
  const online = useOnlineStatus();
  const [view, setView] = useState<WorkspaceView>("pos");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [shift, setShift] = useState<Shift | null>(null);
  const [shiftLoading, setShiftLoading] = useState(true);
  const [shiftError, setShiftError] = useState<string | null>(null);
  const [shiftDialog, setShiftDialog] = useState<"open" | "close" | null>(null);
  const [closedShift, setClosedShift] = useState<Shift | null>(null);

  const loadShift = useCallback(async () => {
    setShiftLoading(true);
    setShiftError(null);
    try {
      setShift(await request<Shift>("/api/shifts/active"));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 404) {
        setShift(null);
        setShiftDialog("open");
      } else {
        const detail = normalizeApiError(caught);
        setShiftError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
      }
    } finally {
      setShiftLoading(false);
    }
  }, [request, t]);

  useEffect(() => {
    let active = true;
    request<Shift>("/api/shifts/active")
      .then((loadedShift) => {
        if (active) setShift(loadedShift);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && caught.status === 404) {
          setShift(null);
          setShiftDialog("open");
        } else {
          const detail = normalizeApiError(caught);
          setShiftError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
        }
      })
      .finally(() => {
        if (active) setShiftLoading(false);
      });
    return () => {
      active = false;
    };
  }, [request, t]);

  const handleShiftComplete = (completedShift: Shift) => {
    if (completedShift.status === "CLOSED") {
      setShift(null);
      setClosedShift(completedShift);
    } else {
      setShift(completedShift);
    }
    setShiftDialog(null);
  };

  const roleLabel = t(user.role);
  const SidebarToggleIcon = locale === "ar" ? PanelRightClose : PanelLeftClose;

  return (
    <div className={`workspace ${sidebarOpen ? "sidebar-is-open" : "sidebar-is-closed"}`}>
      <aside className="sidebar">
        <div className="sidebar-brand"><BrandMark compact={!sidebarOpen} /></div>
        <nav aria-label={t("operations")}>
          <button className={view === "pos" ? "active" : ""} type="button" onClick={() => setView("pos")}>
            <ShoppingCart size={21} /><span>{t("operations")}</span>
          </button>
          <button className={view === "catalog" ? "active" : ""} type="button" onClick={() => setView("catalog")}>
            <PackagePlus size={21} /><span>{t("catalogNav")}</span>
          </button>
          {user.role === "manager" && (
            <button className={view === "dashboard" ? "active" : ""} type="button" onClick={() => setView("dashboard")}>
              <BarChart3 size={21} /><span>{t("dashboard")}</span>
            </button>
          )}
        </nav>
        <div className="sidebar-footer">
          <div className="pharmacy-pill"><Pill size={18} /><span>Tenant #{user.tenant_id}</span></div>
          <small>DawAI v0.1</small>
        </div>
      </aside>

      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-start">
            <button className="icon-button desktop-only" type="button" onClick={() => setSidebarOpen((open) => !open)} aria-label="Toggle sidebar">
              <SidebarToggleIcon size={20} />
            </button>
            <div className={`connection-state ${online ? "is-online" : "is-offline"}`}>
              <i />{online ? t("online") : t("offline")}
            </div>
          </div>

          <div className="topbar-actions">
            <button className="language-button topbar-language" type="button" onClick={toggleLocale}>
              <Languages size={17} />{t("language")}
            </button>
            <button
              className={`shift-chip ${shift ? "open" : "closed"}`}
              type="button"
              onClick={() => setShiftDialog(shift ? "close" : "open")}
              disabled={shiftLoading}
            >
              <Power size={16} />
              {shiftLoading ? "…" : shift ? t("shiftOpen") : t("noOpenShift")}
            </button>
            <div className="user-menu-wrap">
              <button className="user-button" type="button" onClick={() => setUserMenuOpen((open) => !open)} aria-expanded={userMenuOpen}>
                <span className="avatar">{user.full_name.trim().charAt(0).toUpperCase()}</span>
                <span className="user-copy"><strong>{user.full_name}</strong><small>{roleLabel}</small></span>
                <ChevronDown size={16} />
              </button>
              {userMenuOpen && (
                <div className="user-dropdown">
                  <small>{t("signedInAs")}</small>
                  <strong>{user.email}</strong>
                  <button type="button" onClick={() => void logout()}><LogOut size={17} />{t("logout")}</button>
                </div>
              )}
            </div>
          </div>
        </header>

        <nav className="mobile-nav" aria-label={t("operations")}>
          <button className={view === "pos" ? "active" : ""} type="button" onClick={() => setView("pos")}><ShoppingCart size={20} /><span>{t("operations")}</span></button>
          <button className={view === "catalog" ? "active" : ""} type="button" onClick={() => setView("catalog")}><PackagePlus size={20} /><span>{t("catalogNav")}</span></button>
          {user.role === "manager" && <button className={view === "dashboard" ? "active" : ""} type="button" onClick={() => setView("dashboard")}><BarChart3 size={20} /><span>{t("dashboard")}</span></button>}
        </nav>

        <main className="content-area">
          {shiftError && <div className="alert alert-error page-alert">{shiftError}<button type="button" onClick={() => void loadShift()}>{t("retry")}</button></div>}
          {view === "pos" && <PosPage shift={shift} />}
          {view === "catalog" && <CatalogOnboardingPage onOpenPos={() => setView("pos")} />}
          {view === "dashboard" && <DashboardPage />}
        </main>
      </div>

      {shiftDialog && (
        <ShiftDialog
          mode={shiftDialog}
          onDismiss={shiftDialog === "close" ? () => setShiftDialog(null) : undefined}
          onCompleted={handleShiftComplete}
        />
      )}
      {closedShift && <ShiftClosedDialog shift={closedShift} onDone={() => { setClosedShift(null); setShiftDialog("open"); }} />}
    </div>
  );
}
