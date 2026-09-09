import {
  Barcode,
  Box,
  CircleAlert,
  CreditCard,
  Minus,
  PackageOpen,
  Plus,
  ReceiptText,
  Search,
  ShoppingBasket,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { normalizeApiError } from "../../api/client";
import type {
  Customer,
  Invoice,
  InvoiceInput,
  Product,
  ProductBatch,
  Shift,
} from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { useI18n } from "../../i18n/useI18n";
import { formatMoney, lineTotal } from "../../utils/format";
import { ExpiryBadge } from "./ExpiryBadge";
import { InvoiceDialog, type ReceiptLine } from "./InvoiceDialog";

interface CartLine {
  product: Product;
  quantity: number;
  saleUnitPrice: string;
}

function getListUnitPrice(product: Product): string | null {
  if (!product.is_divisible) return product.price;
  return product.part_price;
}

function normalizeSearch(value: string) {
  return value.trim().toLocaleLowerCase();
}

function productsPath(query: string) {
  const normalized = query.trim();
  return normalized
    ? `/api/products/search?q=${encodeURIComponent(normalized)}&limit=20`
    : "/api/products/?limit=20";
}

export function PosPage({ shift }: { shift: Shift | null }) {
  const { request } = useAuth();
  const { locale, t } = useI18n();
  const [products, setProducts] = useState<Product[]>([]);
  const [batches, setBatches] = useState<ProductBatch[]>([]);
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [query, setQuery] = useState("");
  const [cart, setCart] = useState<CartLine[]>([]);
  const [paymentType, setPaymentType] = useState<"cash" | "credit">("cash");
  const [customerId, setCustomerId] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [receiptLines, setReceiptLines] = useState<ReceiptLine[]>([]);
  const searchInputRef = useRef<HTMLInputElement>(null);
  const lastAutoAddedRef = useRef("");
  const pendingSaleRef = useRef<{ fingerprint: string; idempotencyKey: string } | null>(null);
  const searchInitializedRef = useRef(false);

  const loadCatalog = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [loadedProducts, loadedBatches, loadedCustomers] = await Promise.all([
        request<Product[]>(productsPath("")),
        request<ProductBatch[]>("/api/products/batches/?limit=100"),
        request<Customer[]>("/api/customers/?limit=100"),
      ]);
      setProducts(loadedProducts);
      setBatches(loadedBatches);
      setCustomers(loadedCustomers);
    } catch (caught) {
      const detail = normalizeApiError(caught);
      setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
    } finally {
      setLoading(false);
    }
  }, [request, t]);

  useEffect(() => {
    let active = true;
    Promise.all([
      request<Product[]>(productsPath("")),
      request<ProductBatch[]>("/api/products/batches/?limit=100"),
      request<Customer[]>("/api/customers/?limit=100"),
    ])
      .then(([loadedProducts, loadedBatches, loadedCustomers]) => {
        if (!active) return;
        setProducts(loadedProducts);
        setBatches(loadedBatches);
        setCustomers(loadedCustomers);
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

  useEffect(() => {
    if (!searchInitializedRef.current) {
      searchInitializedRef.current = true;
      return;
    }
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setLoading(true);
      request<Product[]>(productsPath(query), { signal: controller.signal })
        .then(setProducts)
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          const detail = normalizeApiError(caught);
          setError(
            detail === "network_unavailable" || detail === "unexpected_error"
              ? t(detail)
              : detail,
          );
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [query, request, t]);

  const nearestBatchByProduct = useMemo(() => {
    const result = new Map<number, ProductBatch>();
    for (const batch of batches) {
      if (!batch.is_sellable || batch.quantity <= 0) continue;
      const current = result.get(batch.product_id);
      if (!current || batch.expiry_date < current.expiry_date) result.set(batch.product_id, batch);
    }
    return result;
  }, [batches]);

  const addProduct = useCallback(
    (product: Product, amount = 1) => {
      setError(null);
      const unitPrice = getListUnitPrice(product);
      if (!unitPrice) {
        setError(locale === "ar" ? "هذا المنتج يحتاج سعر وحدة قبل بيعه." : "This product needs a unit price before it can be sold.");
        return;
      }
      if (product.sellable_parts <= 0) {
        setError(t("stockLimit"));
        return;
      }
      setCart((current) => {
        const existing = current.find((line) => line.product.id === product.id);
        if (existing) {
          if (existing.quantity + amount > product.sellable_parts) {
            setError(t("stockLimit"));
            return current;
          }
          return current.map((line) =>
            line.product.id === product.id ? { ...line, quantity: line.quantity + amount } : line,
          );
        }
        if (amount > product.sellable_parts) {
          setError(t("stockLimit"));
          return current;
        }
        return [...current, { product, quantity: amount, saleUnitPrice: unitPrice }];
      });
      window.setTimeout(() => searchInputRef.current?.focus(), 0);
    },
    [locale, t],
  );

  useEffect(() => {
    const normalized = normalizeSearch(query);
    if (!normalized) {
      lastAutoAddedRef.current = "";
      return;
    }
    if (normalized === lastAutoAddedRef.current) return;
    const exact = products.find((product) => product.barcode?.toLocaleLowerCase() === normalized);
    if (!exact) return;
    const timeout = window.setTimeout(() => {
      lastAutoAddedRef.current = normalized;
      addProduct(exact);
      setQuery("");
    }, 120);
    return () => window.clearTimeout(timeout);
  }, [addProduct, products, query]);

  const cartTotal = useMemo(
    () => cart.reduce((total, line) => total + lineTotal(line.saleUnitPrice, line.quantity), 0),
    [cart],
  );

  const updateQuantity = (productId: number, nextQuantity: number) => {
    setError(null);
    setCart((current) =>
      current
        .map((line) => {
          if (line.product.id !== productId) return line;
          if (nextQuantity > line.product.sellable_parts) {
            setError(t("stockLimit"));
            return line;
          }
          return { ...line, quantity: nextQuantity };
        })
        .filter((line) => line.quantity > 0),
    );
  };

  const updatePrice = (productId: number, price: string) => {
    setCart((current) =>
      current.map((line) =>
        line.product.id === productId ? { ...line, saleUnitPrice: price } : line,
      ),
    );
  };

  const removeLine = (productId: number) => {
    setCart((current) => current.filter((line) => line.product.id !== productId));
  };

  const completeSale = async () => {
    setError(null);
    if (!shift) {
      setError(t("shiftRequiredBody"));
      return;
    }
    if (cart.length === 0) return;
    if (paymentType === "credit" && !customerId) {
      setError(t("customerRequired"));
      return;
    }
    for (const line of cart) {
      const price = Number(line.saleUnitPrice);
      const listPrice = Number(getListUnitPrice(line.product));
      if (!Number.isFinite(price) || price < 0) {
        setError(t("invalidAmount"));
        return;
      }
      if (price > listPrice) {
        setError(t("priceTooHigh"));
        return;
      }
    }

    const saleDetails = {
      payment_type: paymentType,
      customer_id: paymentType === "credit" ? Number(customerId) : null,
      items: cart.map((line) => ({
        product_id: line.product.id,
        quantity: line.quantity,
        sale_unit_price: Number(line.saleUnitPrice).toFixed(2),
      })),
    };
    const fingerprint = JSON.stringify(saleDetails);
    if (!pendingSaleRef.current || pendingSaleRef.current.fingerprint !== fingerprint) {
      pendingSaleRef.current = { fingerprint, idempotencyKey: crypto.randomUUID() };
    }
    const payload: InvoiceInput = {
      idempotency_key: pendingSaleRef.current.idempotencyKey,
      ...saleDetails,
    };

    setSubmitting(true);
    try {
      const created = await request<Invoice>("/api/sales/", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setReceiptLines(cart.map((line) => ({ productId: line.product.id, name: line.product.name })));
      setInvoice(created);
      pendingSaleRef.current = null;
      setCart([]);
      setPaymentType("cash");
      setCustomerId("");
      await loadCatalog();
    } catch (caught) {
      const detail = normalizeApiError(caught);
      setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <section className="pos-page page-enter">
      <div className="catalog-panel">
        <header className="pos-heading">
          <div>
            <span className="eyebrow"><ShoppingBasket size={15} />{t("operations")}</span>
            <h1>{t("products")}</h1>
          </div>
          <small>{t("catalogSearchNotice")}</small>
        </header>

        <div className="search-box">
          <Search size={21} />
          <input
            ref={searchInputRef}
            type="search"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              if (!event.target.value) lastAutoAddedRef.current = "";
            }}
            placeholder={t("searchPlaceholder")}
            autoComplete="off"
            autoFocus
            aria-label={t("searchPlaceholder")}
          />
          {query && <button className="icon-button" type="button" onClick={() => setQuery("")} aria-label={t("close")}><X size={18} /></button>}
          <div className="scanner-state"><Barcode size={18} /><span><strong>{t("scannerReady")}</strong><small>{t("scannerHelp")}</small></span></div>
        </div>

        {error && <div className="alert alert-error pos-alert" role="alert"><CircleAlert size={18} />{error}<button type="button" onClick={() => setError(null)} aria-label={t("close")}><X size={16} /></button></div>}

        {loading ? (
          <div className="product-grid" aria-busy="true">{Array.from({ length: 8 }, (_, index) => <div className="skeleton product-skeleton" key={index} />)}</div>
        ) : products.length === 0 ? (
          <div className="state-panel empty-products"><PackageOpen size={34} /><h2>{t("noProducts")}</h2><p>{t("noProductsHint")}</p></div>
        ) : (
          <div className="product-grid">
            {products.map((product) => {
              const nearestBatch = nearestBatchByProduct.get(product.id);
              const soldUnitLabel = product.is_divisible ? product.part_name || t("part") : t("box");
              const unitPrice = getListUnitPrice(product);
              return (
                <article className={`product-card ${product.sellable_parts <= 0 ? "disabled" : ""}`} key={product.id}>
                  <div className="product-card-top">
                    <span className="category-chip">{product.category.replaceAll("_", " ")}</span>
                    <ExpiryBadge batch={nearestBatch} />
                  </div>
                  <div className="product-copy">
                    <h2>{product.name}</h2>
                    <span className="barcode-text"><Barcode size={14} />{product.barcode || "—"}</span>
                  </div>
                  <div className="stock-line">
                    <span>{product.sellable_parts > 0 ? t("inStock") : t("outOfStock")}</span>
                    <strong>{product.available_boxes} {t("box")}{product.is_divisible ? ` + ${product.available_parts} ${product.part_name || t("part")}` : ""}</strong>
                  </div>
                  {(product.expired_parts > 0 || product.quarantined_parts > 0) && (
                    <div className="stock-warnings">
                      {product.expired_parts > 0 && <span>{t("expiredStock")}: {product.expired_parts}</span>}
                      {product.quarantined_parts > 0 && <span>{t("quarantineStock")}: {product.quarantined_parts}</span>}
                    </div>
                  )}
                  <div className="product-price"><strong>{unitPrice ? formatMoney(unitPrice, locale) : "—"}</strong><small>/ {soldUnitLabel}</small></div>
                  <div className="product-actions">
                    {product.is_divisible && (
                      <button className="button button-secondary" type="button" onClick={() => addProduct(product, 1)} disabled={product.sellable_parts < 1 || !unitPrice}><Plus size={16} />{t("addPart")}</button>
                    )}
                    <button className="button button-primary" type="button" onClick={() => addProduct(product, product.is_divisible ? product.parts_per_unit : 1)} disabled={product.sellable_parts < (product.is_divisible ? product.parts_per_unit : 1) || !unitPrice}><Box size={16} />{t("addBox")}</button>
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </div>

      <aside className="cart-panel">
        <header className="cart-heading">
          <div><ReceiptText size={20} /><h2>{t("cart")}</h2><span>{cart.length}</span></div>
          {cart.length > 0 && <button type="button" onClick={() => setCart([])}><Trash2 size={16} />{t("clearCart")}</button>}
        </header>

        <div className="cart-lines">
          {cart.length === 0 ? (
            <div className="cart-empty"><span><ShoppingBasket size={30} /></span><h3>{t("cartEmpty")}</h3><p>{t("cartEmptyHint")}</p></div>
          ) : cart.map((line) => {
            const listPrice = getListUnitPrice(line.product);
            return (
              <article className="cart-line" key={line.product.id}>
                <div className="cart-line-heading"><div><strong>{line.product.name}</strong><small>{line.product.barcode || `#${line.product.id}`}</small></div><button className="icon-button" type="button" onClick={() => removeLine(line.product.id)} aria-label={t("remove")}><Trash2 size={16} /></button></div>
                <div className="cart-line-controls">
                  <div className="quantity-control" aria-label={t("quantity")}>
                    <button type="button" onClick={() => updateQuantity(line.product.id, line.quantity - 1)}><Minus size={15} /></button>
                    <input aria-label={t("quantity")} type="number" min="1" max={line.product.sellable_parts} value={line.quantity} onChange={(event) => updateQuantity(line.product.id, Number(event.target.value))} />
                    <button type="button" onClick={() => updateQuantity(line.product.id, line.quantity + 1)}><Plus size={15} /></button>
                  </div>
                  <label className="price-input"><span>{t("unitPrice")}</span><input aria-label={`${t("unitPrice")} — ${line.product.name}`} type="number" min="0" max={listPrice ?? undefined} step="0.01" value={line.saleUnitPrice} onChange={(event) => updatePrice(line.product.id, event.target.value)} /></label>
                </div>
                <div className="cart-line-total"><span>{t("subtotal")}</span><strong>{formatMoney(lineTotal(line.saleUnitPrice || "0", line.quantity), locale)}</strong></div>
              </article>
            );
          })}
        </div>

        <div className="checkout-panel">
          <div className="payment-tabs" role="group" aria-label={t("paymentMethod")}>
            <button className={paymentType === "cash" ? "active" : ""} type="button" onClick={() => setPaymentType("cash")}><Wallet size={17} />{t("cash")}</button>
            <button className={paymentType === "credit" ? "active" : ""} type="button" onClick={() => setPaymentType("credit")}><CreditCard size={17} />{t("credit")}</button>
          </div>
          {paymentType === "credit" && (
            <label className="customer-select"><span>{t("customer")}</span><select value={customerId} onChange={(event) => setCustomerId(event.target.value)}><option value="">{t("selectCustomer")}</option>{customers.map((customer) => <option value={customer.id} key={customer.id}>{customer.name}{customer.phone ? ` — ${customer.phone}` : ""}</option>)}</select></label>
          )}
          <div className="checkout-total"><span>{t("total")}<small>{t("itemsCount", { count: cart.length })}</small></span><strong>{formatMoney(cartTotal, locale)}</strong></div>
          <button className="button button-primary checkout-button" type="button" onClick={() => void completeSale()} disabled={!shift || cart.length === 0 || submitting}>
            <ReceiptText size={19} />{submitting ? t("completingSale") : t("completeSale")}
          </button>
          {!shift && <p className="shift-required-note"><CircleAlert size={15} />{t("shiftRequiredTitle")}</p>}
        </div>
      </aside>

      {invoice && <InvoiceDialog invoice={invoice} receiptLines={receiptLines} onNewSale={() => { setInvoice(null); setReceiptLines([]); window.setTimeout(() => searchInputRef.current?.focus(), 0); }} />}
    </section>
  );
}
