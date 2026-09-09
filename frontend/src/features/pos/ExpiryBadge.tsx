import { CalendarClock, CircleAlert, ShieldCheck } from "lucide-react";

import type { ProductBatch } from "../../api/types";
import { useI18n } from "../../i18n/useI18n";

export function ExpiryBadge({ batch }: { batch?: ProductBatch }) {
  const { t } = useI18n();
  if (!batch) return null;

  const label =
    batch.days_until_expiry === 0
      ? t("expiresToday")
      : batch.expiry_status === "critical"
        ? t("expiryCritical")
        : batch.expiry_status === "warning"
          ? t("expiryWarning")
          : t("expiryValid");
  const Icon = batch.expiry_status === "critical" ? CircleAlert : batch.expiry_status === "warning" ? CalendarClock : ShieldCheck;

  return (
    <span className={`expiry-badge expiry-${batch.expiry_status}`} title={t("expiresIn", { days: batch.days_until_expiry })}>
      <Icon size={14} />{label}
    </span>
  );
}
