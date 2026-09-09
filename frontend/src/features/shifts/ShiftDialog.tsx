import { CircleAlert, LockKeyhole, X } from "lucide-react";
import { useState, type FormEvent } from "react";

import { normalizeApiError } from "../../api/client";
import type { Shift } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { useI18n } from "../../i18n/useI18n";
import { formatMoney } from "../../utils/format";

interface ShiftDialogProps {
  mode: "open" | "close";
  onDismiss?: () => void;
  onCompleted: (shift: Shift) => void;
}

function isValidAmount(value: string) {
  return value.trim() !== "" && Number.isFinite(Number(value)) && Number(value) >= 0;
}

export function ShiftDialog({ mode, onDismiss, onCompleted }: ShiftDialogProps) {
  const { request } = useAuth();
  const { locale, t } = useI18n();
  const [amount, setAmount] = useState("0.00");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    if (!isValidAmount(amount)) {
      setError(t("invalidAmount"));
      return;
    }
    setSubmitting(true);
    try {
      const shift = await request<Shift>(`/api/shifts/${mode}`, {
        method: "POST",
        body: JSON.stringify(
          mode === "open"
            ? { opening_balance: Number(amount).toFixed(2) }
            : { actual_closing_balance: Number(amount).toFixed(2) },
        ),
      });
      onCompleted(shift);
    } catch (caught) {
      const detail = normalizeApiError(caught);
      setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="dialog-backdrop" role="presentation">
      <section className="dialog-card" role="dialog" aria-modal="true" aria-labelledby="shift-title">
        {onDismiss && (
          <button className="icon-button dialog-close" type="button" onClick={onDismiss} aria-label={t("close")}>
            <X size={19} />
          </button>
        )}
        <span className="dialog-icon"><LockKeyhole size={23} /></span>
        <h2 id="shift-title">{mode === "open" ? t("openShift") : t("closeShift")}</h2>
        <p>{mode === "open" ? t("shiftRequiredBody") : t("closeShiftWarning")}</p>

        <form onSubmit={handleSubmit}>
          <label className="money-field">
            <span>{mode === "open" ? t("openingBalance") : t("actualClosingBalance")}</span>
            <div>
              <input
                aria-label={mode === "open" ? t("openingBalance") : t("actualClosingBalance")}
                type="number"
                min="0"
                step="0.01"
                inputMode="decimal"
                value={amount}
                onChange={(event) => setAmount(event.target.value)}
                autoFocus
              />
              <small>{t("egp")}</small>
            </div>
          </label>
          <div className="amount-preview">{formatMoney(isValidAmount(amount) ? amount : 0, locale)}</div>
          {error && <div className="alert alert-error" role="alert"><CircleAlert size={17} />{error}</div>}
          <button className="button button-primary button-large" type="submit" disabled={submitting}>
            {submitting ? (mode === "open" ? t("openingShift") : t("closingShift")) : mode === "open" ? t("openShift") : t("closeShift")}
          </button>
        </form>
      </section>
    </div>
  );
}
