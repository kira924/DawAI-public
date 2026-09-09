import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { CatalogProduct, Product } from "../../api/types";
import type { AuthContextValue } from "../../auth/context";
import { renderWithProviders } from "../../test/render";
import { CatalogOnboardingPage } from "./CatalogOnboardingPage";

const catalogProduct: CatalogProduct = {
  id: 501,
  display_name: "Panadol Extra",
  name_en: "Panadol Extra",
  name_ar: "بانادول إكسترا",
  active_ingredients: "Paracetamol, Caffeine",
  manufacturer: "Synthetic Pharma",
  reference_price: "50.00",
  units_per_package: 2,
  package_size: 20,
  package_unit: "tablet",
  dosage_form: "tablet",
  therapeutic_category: null,
  barcode: "4006381333931",
  data_quality_flags: ["branch_only"],
  needs_review: true,
};

const createdProduct: Product = {
  id: 88,
  tenant_id: 2,
  catalog_product_id: catalogProduct.id,
  name: "Panadol Extra",
  barcode: catalogProduct.barcode,
  category: "medicine",
  price: "50.00",
  is_divisible: false,
  parts_per_unit: 1,
  part_name: null,
  part_price: null,
  expiry_date: null,
  return_policy: "standard",
  total_parts: 0,
  sellable_parts: 0,
  expired_parts: 0,
  quarantined_parts: 0,
  tracked_physical_parts: 0,
  available_boxes: 0,
  available_parts: 0,
};

function setup() {
  window.localStorage.setItem("dawai.locale", "en");
  const calls: Array<{ path: string; init?: RequestInit }> = [];
  const request: AuthContextValue["request"] = async <T,>(path: string, init?: RequestInit) => {
    calls.push({ path, init });
    if (path.startsWith("/api/catalog/products/search")) return [catalogProduct] as T;
    if (path === "/api/products/") {
      const payload = JSON.parse(String(init?.body)) as { initial_boxes: number; initial_parts: number };
      return {
        ...createdProduct,
        total_parts: payload.initial_boxes * 2 + payload.initial_parts,
      } as T;
    }
    throw new Error(`Unexpected request: ${path}`);
  };
  renderWithProviders(<CatalogOnboardingPage onOpenPos={() => undefined} />, { request });
  return { calls };
}

async function searchAndSelect() {
  const user = userEvent.setup();
  await user.type(screen.getByPlaceholderText(/Search by Arabic or English name/), "pana");
  await user.click(await screen.findByRole("button", { name: "Add to inventory" }));
  return user;
}

describe("CatalogOnboardingPage", () => {
  it("searches the shared catalog and exposes bilingual provenance warnings", async () => {
    const { calls } = setup();
    const user = userEvent.setup();

    await user.type(screen.getByPlaceholderText(/Search by Arabic or English name/), "pana");

    expect(await screen.findByRole("heading", { name: "Panadol Extra" })).toBeInTheDocument();
    expect(screen.getByText("بانادول إكسترا")).toBeInTheDocument();
    expect(screen.getByText("Synthetic Pharma")).toBeInTheDocument();
    expect(screen.getByText("Needs review")).toBeInTheDocument();
    expect(calls[0]?.path).toBe("/api/catalog/products/search?q=pana&limit=20");
  });

  it("creates a tenant product without silently creating opening stock", async () => {
    const { calls } = setup();
    const user = await searchAndSelect();

    await user.click(screen.getByRole("button", { name: "Create inventory product" }));

    expect(await screen.findByRole("heading", { name: "Product added" })).toBeInTheDocument();
    const createCall = calls.find((call) => call.path === "/api/products/");
    expect(JSON.parse(String(createCall?.init?.body))).toMatchObject({
      catalog_product_id: 501,
      name: "Panadol Extra",
      barcode: "4006381333931",
      price: "50.00",
      initial_boxes: 0,
      initial_parts: 0,
      initial_batch: null,
    });
  });

  it("requires and submits batch-backed opening stock when explicitly selected", async () => {
    const { calls } = setup();
    const user = await searchAndSelect();

    await user.click(screen.getByRole("checkbox", { name: /Add opening stock now/ }));
    await user.clear(screen.getByRole("spinbutton", { name: "Boxes" }));
    await user.type(screen.getByRole("spinbutton", { name: "Boxes" }), "3");
    await user.type(screen.getByRole("textbox", { name: "Batch number" }), "PAN-2028-A");
    await user.type(screen.getByLabelText("Expiry date"), "2028-12-31");
    await user.click(screen.getByRole("button", { name: "Create inventory product" }));

    await waitFor(() => expect(calls.some((call) => call.path === "/api/products/")).toBe(true));
    const createCall = calls.find((call) => call.path === "/api/products/");
    expect(JSON.parse(String(createCall?.init?.body))).toMatchObject({
      is_divisible: false,
      initial_boxes: 3,
      initial_parts: 0,
      initial_batch: { batch_number: "PAN-2028-A", expiry_date: "2028-12-31" },
    });
  });
});
