import { Check, Printer, ReceiptText, X } from "lucide-react";

import type { Invoice } from "../../api/types";
import { useI18n } from "../../i18n/useI18n";
import { formatDateTime, formatMoney } from "../../utils/format";

export interface ReceiptLine {
  productId: number;
  name: string;
}

export function InvoiceDialog({
  invoice,
  receiptLines,
  onNewSale,
}: {
  invoice: Invoice;
  receiptLines: ReceiptLine[];
  onNewSale: () => void;
}) {
  const { locale, t } = useI18n();
  const names = new Map(receiptLines.map((line) => [line.productId, line.name]));

  return (
    <div className="dialog-backdrop receipt-backdrop" role="presentation">
      <section className="receipt-dialog" role="dialog" aria-modal="true" aria-labelledby="invoice-title">
        <button className="icon-button dialog-close no-print" type="button" onClick={onNewSale} aria-label={t("close")}><X size={19} /></button>
        <div className="receipt-success"><Check size={21} /></div>
        <p className="receipt-status">{t("invoiceSuccess")}</p>
        <div className="receipt-brand"><span>D</span><strong>DawAI</strong></div>
        <h2 id="invoice-title">{t("invoiceNumber", { id: invoice.id })}</h2>
        <div className="receipt-meta">
          <div><span>{t("invoiceDate")}</span><strong>{formatDateTime(invoice.created_at, locale)}</strong></div>
          <div><span>{t("paymentMethod")}</span><strong>{t(invoice.payment_type)}</strong></div>
        </div>
        <div className="receipt-lines">
          {invoice.items.map((item) => (
            <div className="receipt-line" key={item.id}>
              <div><strong>{names.get(item.product_id) ?? `#${item.product_id}`}</strong><small>{item.quantity} × {formatMoney(item.sale_unit_price, locale)}</small></div>
              <strong>{formatMoney(item.subtotal, locale)}</strong>
            </div>
          ))}
        </div>
        <div className="receipt-total"><span>{t("total")}</span><strong>{formatMoney(invoice.total_amount, locale)}</strong></div>
        {Number(invoice.outstanding_amount) > 0 && <div className="receipt-due"><span>{t("amountDue")}</span><strong>{formatMoney(invoice.outstanding_amount, locale)}</strong></div>}
        <div className="receipt-actions no-print">
          <button className="button button-secondary" type="button" onClick={() => window.print()}><Printer size={17} />{t("print")}</button>
          <button className="button button-primary" type="button" onClick={onNewSale}><ReceiptText size={17} />{t("newSale")}</button>
        </div>
      </section>
    </div>
  );
}
