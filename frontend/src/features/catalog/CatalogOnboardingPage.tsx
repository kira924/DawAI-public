import {
  AlertTriangle,
  ArrowRight,
  Barcode,
  Box,
  CheckCircle2,
  PackagePlus,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import { useEffect, useMemo, useState, type FormEvent } from "react";

import { ApiError, normalizeApiError } from "../../api/client";
import type { CatalogProduct, Product, ProductInput } from "../../api/types";
import { useAuth } from "../../auth/useAuth";
import { useI18n } from "../../i18n/useI18n";

type ReturnPolicy = ProductInput["return_policy"];

interface OnboardingForm {
  name: string;
  barcode: string;
  price: string;
  returnPolicy: ReturnPolicy;
  isDivisible: boolean;
  partsPerUnit: string;
  partName: string;
  partPrice: string;
  addOpeningStock: boolean;
  initialBoxes: string;
  initialParts: string;
  batchNumber: string;
  expiryDate: string;
  confirmNearExpiry: boolean;
}

const emptyForm: OnboardingForm = {
  name: "",
  barcode: "",
  price: "",
  returnPolicy: "standard",
  isDivisible: false,
  partsPerUnit: "1",
  partName: "",
  partPrice: "",
  addOpeningStock: false,
  initialBoxes: "0",
  initialParts: "0",
  batchNumber: "",
  expiryDate: "",
  confirmNearExpiry: false,
};

function localizedName(product: CatalogProduct, locale: "ar" | "en") {
  return (locale === "ar" ? product.name_ar : product.name_en) ?? product.display_name;
}

function secondaryName(product: CatalogProduct, locale: "ar" | "en") {
  const name = locale === "ar" ? product.name_en : product.name_ar;
  return name && name !== localizedName(product, locale) ? name : null;
}

function nonNegativeInteger(value: string) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

export function CatalogOnboardingPage({ onOpenPos }: { onOpenPos: () => void }) {
  const { request, user } = useAuth();
  const { locale, t } = useI18n();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CatalogProduct[]>([]);
  const [searching, setSearching] = useState(false);
  const [selected, setSelected] = useState<CatalogProduct | null>(null);
  const [form, setForm] = useState<OnboardingForm>(emptyForm);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<Product | null>(null);

  const normalizedQuery = query.trim();

  useEffect(() => {
    if (normalizedQuery.length < 2) {
      return;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      setError(null);
      request<CatalogProduct[]>(
        `/api/catalog/products/search?q=${encodeURIComponent(normalizedQuery)}&limit=20`,
        { signal: controller.signal },
      )
        .then(setResults)
        .catch((caught: unknown) => {
          if (caught instanceof DOMException && caught.name === "AbortError") return;
          const detail = normalizeApiError(caught);
          setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 250);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [normalizedQuery, request, t]);

  const updateQuery = (value: string) => {
    setQuery(value);
    if (value.trim().length < 2) {
      setResults([]);
      setSearching(false);
    }
  };

  const selectProduct = (product: CatalogProduct) => {
    const suggestedParts = product.units_per_package && product.units_per_package > 1
      ? String(product.units_per_package)
      : "1";
    setSelected(product);
    setCreated(null);
    setError(null);
    setForm({
      ...emptyForm,
      name: localizedName(product, locale),
      barcode: product.barcode ?? "",
      price: product.reference_price ?? "",
      partsPerUnit: suggestedParts,
    });
  };

  const openingQuantity = useMemo(() => {
    const boxes = nonNegativeInteger(form.initialBoxes) ?? 0;
    const parts = nonNegativeInteger(form.initialParts) ?? 0;
    return boxes * (form.isDivisible ? Number(form.partsPerUnit) || 1 : 1) + parts;
  }, [form.initialBoxes, form.initialParts, form.isDivisible, form.partsPerUnit]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected) return;

    const price = Number(form.price);
    const partsPerUnit = nonNegativeInteger(form.partsPerUnit);
    const initialBoxes = nonNegativeInteger(form.initialBoxes);
    const initialParts = nonNegativeInteger(form.initialParts);
    if (!form.name.trim() || !Number.isFinite(price) || price < 0) {
      setError(t("catalogInvalidCoreFields"));
      return;
    }
    if (form.isDivisible && (!partsPerUnit || partsPerUnit < 2)) {
      setError(t("catalogInvalidParts"));
      return;
    }
    if (form.addOpeningStock && (initialBoxes === null || initialParts === null)) {
      setError(t("catalogInvalidStock"));
      return;
    }
    if (form.addOpeningStock && openingQuantity <= 0) {
      setError(t("catalogOpeningStockPositive"));
      return;
    }
    if (form.addOpeningStock && openingQuantity > 0 && (!form.batchNumber.trim() || !form.expiryDate)) {
      setError(t("catalogBatchRequired"));
      return;
    }

    const payload: ProductInput = {
      name: form.name.trim(),
      barcode: form.barcode.trim() || null,
      catalog_product_id: selected.id,
      category: "medicine",
      price: price.toFixed(2),
      is_divisible: form.isDivisible,
      parts_per_unit: form.isDivisible ? (partsPerUnit ?? 1) : 1,
      part_name: form.isDivisible ? form.partName.trim() || null : null,
      part_price: form.isDivisible && form.partPrice.trim() ? Number(form.partPrice).toFixed(2) : null,
      expiry_date: null,
      return_policy: form.returnPolicy,
      initial_boxes: form.addOpeningStock ? (initialBoxes ?? 0) : 0,
      initial_parts: form.addOpeningStock ? (initialParts ?? 0) : 0,
      initial_batch: form.addOpeningStock && openingQuantity > 0
        ? { batch_number: form.batchNumber.trim(), expiry_date: form.expiryDate }
        : null,
      confirm_near_expiry: form.addOpeningStock && form.confirmNearExpiry,
    };

    setSubmitting(true);
    setError(null);
    try {
      setCreated(await request<Product>("/api/products/", {
        method: "POST",
        body: JSON.stringify(payload),
      }));
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 409) {
        setError(t("catalogAlreadyLinked"));
      } else {
        const detail = normalizeApiError(caught);
        setError(detail === "network_unavailable" || detail === "unexpected_error" ? t(detail) : detail);
      }
    } finally {
      setSubmitting(false);
    }
  };

  const dismissForm = () => {
    setSelected(null);
    setCreated(null);
    setError(null);
    setForm(emptyForm);
  };

  return (
    <section className="catalog-onboarding-page">
      <header className="catalog-onboarding-heading">
        <div>
          <span className="eyebrow"><PackagePlus size={16} />{t("catalogEyebrow")}</span>
          <h1>{t("catalogTitle")}</h1>
          <p>{t("catalogSubtitle")}</p>
        </div>
        <div className="catalog-safety-note"><ShieldCheck size={20} /><span>{t("catalogTenantNote")}</span></div>
      </header>

      <div className="catalog-onboarding-layout">
        <div className="catalog-browser">
          <label className="catalog-search-box">
            <Search size={21} />
            <input
              type="search"
              value={query}
              onChange={(event) => updateQuery(event.target.value)}
              placeholder={t("catalogSearchPlaceholder")}
              autoFocus
            />
            {query && <button type="button" onClick={() => updateQuery("")} aria-label={t("clearSearch")}><X size={18} /></button>}
          </label>

          {error && !selected && <div className="alert alert-error catalog-error"><AlertTriangle size={18} />{error}</div>}

          {normalizedQuery.length < 2 ? (
            <div className="catalog-empty-state"><Search size={30} /><h2>{t("catalogSearchStart")}</h2><p>{t("catalogSearchHelp")}</p></div>
          ) : searching ? (
            <div className="catalog-result-list" aria-label={t("loadingProducts")}>
              {Array.from({ length: 5 }, (_, index) => <div className="catalog-result-skeleton skeleton" key={index} />)}
            </div>
          ) : results.length === 0 ? (
            <div className="catalog-empty-state"><Box size={30} /><h2>{t("catalogNoResults")}</h2><p>{t("catalogNoResultsHelp")}</p></div>
          ) : (
            <div className="catalog-result-list">
              {results.map((product) => (
                <article className={`catalog-result-card ${selected?.id === product.id ? "selected" : ""}`} key={product.id}>
                  <div className="catalog-result-main">
                    <div className="catalog-result-title">
                      <span className="catalog-medicine-icon"><PackagePlus size={20} /></span>
                      <div><h2>{localizedName(product, locale)}</h2>{secondaryName(product, locale) && <p>{secondaryName(product, locale)}</p>}</div>
                    </div>
                    {product.needs_review && <span className="review-chip"><AlertTriangle size={13} />{t("catalogNeedsReview")}</span>}
                  </div>
                  <dl className="catalog-result-meta">
                    {product.manufacturer && <div><dt>{t("manufacturer")}</dt><dd>{product.manufacturer}</dd></div>}
                    {product.active_ingredients && <div><dt>{t("activeIngredients")}</dt><dd>{product.active_ingredients}</dd></div>}
                    <div><dt>{t("referencePrice")}</dt><dd>{product.reference_price ? `${product.reference_price} ${t("egp")}` : t("needsEntry")}</dd></div>
                    {(product.dosage_form || product.package_size || product.package_unit) && <div><dt>{t("packageDetails")}</dt><dd>{[product.dosage_form, product.package_size, product.package_unit].filter(Boolean).join(" · ")}</dd></div>}
                  </dl>
                  <button className="button button-secondary catalog-add-button" type="button" onClick={() => selectProduct(product)}>
                    {t("addToInventory")}<ArrowRight size={16} />
                  </button>
                </article>
              ))}
            </div>
          )}
        </div>

        <aside className={`catalog-onboarding-form-panel ${selected ? "is-open" : ""}`}>
          {!selected ? (
            <div className="catalog-selection-placeholder"><PackagePlus size={31} /><h2>{t("catalogSelectProduct")}</h2><p>{t("catalogSelectProductHelp")}</p></div>
          ) : created ? (
            <div className="catalog-created-state">
              <span><CheckCircle2 size={36} /></span>
              <h2>{t("catalogCreated")}</h2>
              <p>{created.name}</p>
              <small>{created.total_parts > 0 ? t("catalogCreatedWithStock") : t("catalogCreatedWithoutStock")}</small>
              <button className="button button-primary" type="button" onClick={onOpenPos}>{t("openPointOfSale")}</button>
              <button className="button button-secondary" type="button" onClick={dismissForm}>{t("addAnotherProduct")}</button>
            </div>
          ) : (
            <form onSubmit={submit}>
              <div className="catalog-form-heading">
                <div><span>{t("catalogConfigure")}</span><h2>{localizedName(selected, locale)}</h2></div>
                <button className="icon-button" type="button" onClick={dismissForm} aria-label={t("close")}><X size={19} /></button>
              </div>

              {selected.needs_review && <div className="catalog-review-warning"><AlertTriangle size={18} /><span><strong>{t("catalogReviewTitle")}</strong>{t("catalogReviewBody")}</span></div>}
              {error && <div className="alert alert-error"><AlertTriangle size={18} />{error}</div>}

              <div className="catalog-form-section">
                <h3>{t("catalogIdentityPricing")}</h3>
                <label><span>{t("productName")}</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} required /></label>
                <div className="catalog-form-row">
                  <label><span>{t("barcode")}</span><div className="input-with-icon"><Barcode size={16} /><input value={form.barcode} onChange={(event) => setForm({ ...form, barcode: event.target.value })} /></div></label>
                  <label><span>{t("sellingPrice")}</span><div className="money-field"><input type="number" min="0" step="0.01" value={form.price} onChange={(event) => setForm({ ...form, price: event.target.value })} required /><small>{t("egp")}</small></div></label>
                </div>
                <label><span>{t("returnPolicy")}</span><select value={form.returnPolicy} onChange={(event) => setForm({ ...form, returnPolicy: event.target.value as ReturnPolicy })}><option value="standard">{t("returnStandard")}</option><option value="unopened_only">{t("returnUnopened")}</option><option value="non_returnable">{t("returnNone")}</option></select></label>
              </div>

              <div className="catalog-form-section">
                <label className="catalog-toggle"><input type="checkbox" checked={form.isDivisible} onChange={(event) => setForm({ ...form, isDivisible: event.target.checked })} /><span><strong>{t("divisibleProduct")}</strong><small>{t("divisibleProductHelp")}</small></span></label>
                {form.isDivisible && <div className="catalog-form-row three"><label><span>{t("partsPerBox")}</span><input type="number" min="2" step="1" value={form.partsPerUnit} onChange={(event) => setForm({ ...form, partsPerUnit: event.target.value })} /></label><label><span>{t("partName")}</span><input value={form.partName} onChange={(event) => setForm({ ...form, partName: event.target.value })} placeholder={t("partNameExample")} /></label><label><span>{t("partPrice")}</span><input type="number" min="0" step="0.01" value={form.partPrice} onChange={(event) => setForm({ ...form, partPrice: event.target.value })} /></label></div>}
              </div>

              <div className="catalog-form-section">
                <label className="catalog-toggle"><input type="checkbox" checked={form.addOpeningStock} onChange={(event) => setForm({ ...form, addOpeningStock: event.target.checked })} /><span><strong>{t("addOpeningStock")}</strong><small>{t("addOpeningStockHelp")}</small></span></label>
                {form.addOpeningStock && <div className="catalog-stock-fields">
                  <div className="catalog-form-row"><label><span>{t("initialBoxes")}</span><input type="number" min="0" step="1" value={form.initialBoxes} onChange={(event) => setForm({ ...form, initialBoxes: event.target.value })} /></label>{form.isDivisible && <label><span>{t("initialParts")}</span><input type="number" min="0" step="1" value={form.initialParts} onChange={(event) => setForm({ ...form, initialParts: event.target.value })} /></label>}</div>
                  {openingQuantity > 0 && <><div className="catalog-form-row"><label><span>{t("batchNumber")}</span><input value={form.batchNumber} onChange={(event) => setForm({ ...form, batchNumber: event.target.value })} required /></label><label><span>{t("expiryDate")}</span><input type="date" value={form.expiryDate} onChange={(event) => setForm({ ...form, expiryDate: event.target.value })} required /></label></div>{user?.role === "manager" ? <label className="catalog-toggle compact"><input type="checkbox" checked={form.confirmNearExpiry} onChange={(event) => setForm({ ...form, confirmNearExpiry: event.target.checked })} /><span><strong>{t("confirmNearExpiry")}</strong><small>{t("confirmNearExpiryHelp")}</small></span></label> : <p className="catalog-manager-note">{t("nearExpiryManagerOnly")}</p>}</>}
                </div>}
              </div>

              <div className="catalog-form-actions"><button className="button button-secondary" type="button" onClick={dismissForm}>{t("cancel")}</button><button className="button button-primary" type="submit" disabled={submitting}>{submitting ? t("catalogCreating") : t("createInventoryProduct")}</button></div>
            </form>
          )}
        </aside>
      </div>
    </section>
  );
}
