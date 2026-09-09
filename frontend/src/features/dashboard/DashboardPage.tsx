import {
  ArrowDownLeft,
  ArrowUpRight,
  CalendarDays,
  CircleDollarSign,
  PackageSearch,
  ReceiptText,
  RefreshCw,
  RotateCcw,
  WalletCards,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { normalizeApiError } from "../../api/client";
import type { DashboardSummary } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { useI18n } from "../../i18n/useI18n";
import { formatBusinessDate, formatMoney } from "../../utils/format";

export function DashboardPage() {
  const { request } = useAuth();
  const { locale, t } = useI18n();
  const [summary, setSummary] = useState<DashboardSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSummary(await request<DashboardSummary>("/api/reports/dashboard"));
    } catch (caught) {
      const detail = normalizeApiError(caught);
      setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
    } finally {
      setLoading(false);
    }
  }, [request, t]);

  useEffect(() => {
    let active = true;
    request<DashboardSummary>("/api/reports/dashboard")
      .then((loadedSummary) => {
        if (active) setSummary(loadedSummary);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        const detail = normalizeApiError(caught);
        setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [request, t]);

  return (
    <section className="dashboard-page page-enter">
      <header className="page-heading dashboard-heading">
        <div>
          <span className="eyebrow"><CalendarDays size={15} />{t("businessDate")}</span>
          <h1>{t("todayOverview")}</h1>
          <p>{t("todayOverviewSubtitle")}</p>
        </div>
        <button className="button button-secondary" type="button" onClick={() => void loadSummary()} disabled={loading}>
          <RefreshCw size={17} className={loading ? "spin" : ""} />
          {t("refresh")}
        </button>
      </header>

      {error && (
        <div className="state-panel error-state" role="alert">
          <CircleDollarSign size={30} />
          <p>{error}</p>
          <button className="button button-secondary" type="button" onClick={() => void loadSummary()}>{t("retry")}</button>
        </div>
      )}

      {!error && loading && <DashboardSkeleton />}

      {!error && !loading && summary && (
        <>
          <div className="business-date-card">
            <div className="date-glyph"><CalendarDays size={24} /></div>
            <div>
              <span>{t("businessDate")}</span>
              <strong>{formatBusinessDate(summary.business_date, locale)}</strong>
            </div>
            <small>{summary.timezone}</small>
          </div>

          <div className="metric-grid">
            <MetricCard icon={<ReceiptText />} label={t("sales")} value={formatMoney(summary.total_sales, locale)} tone="primary" trend={<ArrowUpRight />} />
            <MetricCard icon={<WalletCards />} label={t("expenses")} value={formatMoney(summary.total_expenses, locale)} tone="amber" trend={<ArrowDownLeft />} />
            <MetricCard icon={<RotateCcw />} label={t("returns")} value={formatMoney(summary.total_returns, locale)} tone="rose" />
            <MetricCard icon={<CircleDollarSign />} label={t("netRevenue")} value={formatMoney(summary.net_revenue, locale)} tone="green" trend={<ArrowUpRight />} />
          </div>

          <div className="dashboard-bottom-grid">
            <article className="insight-card low-stock-card">
              <div className="insight-icon"><PackageSearch size={24} /></div>
              <div><span>{t("lowStock")}</span><strong>{summary.low_stock_count}</strong></div>
              <div className="stock-meter"><i style={{ width: `${Math.min(summary.low_stock_count * 8, 100)}%` }} /></div>
            </article>
            <article className="insight-card net-card">
              <span>{t("netRevenue")}</span>
              <strong>{formatMoney(summary.net_revenue, locale)}</strong>
              <small>{t("todayOverviewSubtitle")}</small>
            </article>
          </div>
        </>
      )}
    </section>
  );
}

function MetricCard({
  icon,
  label,
  value,
  tone,
  trend,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  tone: string;
  trend?: React.ReactNode;
}) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <div className="metric-top"><span className="metric-icon">{icon}</span>{trend && <span className="metric-trend">{trend}</span>}</div>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function DashboardSkeleton() {
  return (
    <div className="dashboard-skeleton" aria-busy="true">
      <div className="skeleton skeleton-wide" />
      <div className="metric-grid">{Array.from({ length: 4 }, (_, index) => <div className="skeleton skeleton-card" key={index} />)}</div>
    </div>
  );
}
