import { Check, X } from "lucide-react";

import type { Shift } from "../../api/types";
import { useI18n } from "../../i18n/useI18n";
import { formatMoney } from "../../utils/format";

export function ShiftClosedDialog({ shift, onDone }: { shift: Shift; onDone: () => void }) {
  const { locale, t } = useI18n();
  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog-card shift-result" role="dialog" aria-modal="true" aria-labelledby="closed-title">
        <button className="icon-button dialog-close" type="button" onClick={onDone} aria-label={t("close")}>
          <X size={19} />
        </button>
        <span className="dialog-icon success"><Check size={25} /></span>
        <h2 id="closed-title">{t("shiftClosed")}</h2>
        <div className="shift-totals">
          <div><span>{t("expectedBalance")}</span><strong>{formatMoney(shift.expected_closing_balance, locale)}</strong></div>
          <div><span>{t("actualBalance")}</span><strong>{formatMoney(shift.actual_closing_balance ?? 0, locale)}</strong></div>
          <div className={Number(shift.difference ?? 0) === 0 ? "balanced" : "variance"}>
            <span>{t("difference")}</span><strong>{formatMoney(shift.difference ?? 0, locale)}</strong>
          </div>
        </div>
        <button className="button button-primary" type="button" onClick={onDone}>{t("startNewShift")}</button>
      </section>
    </div>
  );
}
