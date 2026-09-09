import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type {
  Customer,
  Invoice,
  Product,
  ProductBatch,
  Shift,
} from "../../api/types";
import type { AuthContextValue } from "../../auth/context";
import { renderWithProviders } from "../../test/render";
import { PosPage } from "./PosPage";

const product: Product = {
  id: 11,
  tenant_id: 2,
  catalog_product_id: null,
  name: "Panadol Extra",
  barcode: "6223000000111",
  category: "medicine",
  price: "50.00",
  is_divisible: false,
  parts_per_unit: 1,
  part_name: null,
  part_price: null,
  expiry_date: null,
  return_policy: "standard",
  total_parts: 12,
  sellable_parts: 10,
  expired_parts: 2,
  quarantined_parts: 0,
  tracked_physical_parts: 12,
  available_boxes: 10,
  available_parts: 0,
};

const batch: ProductBatch = {
  id: 7,
  batch_number: "PAN-27",
  expiry_date: "2027-12-31",
  quantity: 10,
  product_id: product.id,
  tenant_id: 2,
  days_until_expiry: 482,
  expiry_status: "valid",
  is_sellable: true,
};

const shift: Shift = {
  id: 20,
  status: "OPEN",
  start_time: "2026-09-05T08:00:00Z",
  end_time: null,
  opening_balance: "100.00",
  expected_closing_balance: "0.00",
  actual_closing_balance: null,
  difference: null,
  user_id: 4,
  tenant_id: 2,
  opening_business_date: "2026-09-05",
  closing_business_date: null,
};

const invoice: Invoice = {
  id: 91,
  idempotency_key: "66ab4069-1cf8-4f6d-b6fe-49e7246678ee",
  total_amount: "50.00",
  outstanding_amount: "0.00",
  payment_type: "cash",
  customer_id: null,
  created_at: "2026-09-05T08:15:00Z",
  user_id: 4,
  tenant_id: 2,
  items: [{
    id: 101,
    invoice_id: 91,
    product_id: product.id,
    quantity: 1,
    list_unit_price: "50.00",
    sale_unit_price: "50.00",
    subtotal: "50.00",
  }],
};

describe("PosPage", () => {
  it("completes the cash-sale path and displays the invoice", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    const calls: Array<{ path: string; init?: RequestInit }> = [];
    const request: AuthContextValue["request"] = async <T,>(path: string, init?: RequestInit) => {
      calls.push({ path, init });
      if (path.startsWith("/api/products/batches")) return [batch] as T;
      if (path.startsWith("/api/products/")) return [product] as T;
      if (path.startsWith("/api/customers/")) return [] as Customer[] as T;
      if (path === "/api/sales/") return invoice as T;
      throw new Error(`Unexpected request: ${path}`);
    };

    const user = userEvent.setup();
    renderWithProviders(<PosPage shift={shift} />, { request });

    await user.click(await screen.findByRole("button", { name: "Add box" }));
    expect(screen.getAllByText("Panadol Extra")).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Complete sale" }));

    expect(await screen.findByText("Invoice recorded successfully")).toBeInTheDocument();
    const saleCall = calls.find((call) => call.path === "/api/sales/");
    expect(saleCall).toBeDefined();
    const salePayload = JSON.parse(String(saleCall?.init?.body));
    expect(salePayload).toEqual({
      idempotency_key: expect.any(String),
      payment_type: "cash",
      customer_id: null,
      items: [{ product_id: 11, quantity: 1, sale_unit_price: "50.00" }],
    });
    expect(salePayload.idempotency_key).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
    await waitFor(() =>
      expect(
        calls.filter((call) => call.path.startsWith("/api/products/?")).length,
      ).toBeGreaterThan(1),
    );
  });

  it("reuses the idempotency key when an unchanged sale is retried", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    const salePayloads: Array<Record<string, unknown>> = [];
    const request: AuthContextValue["request"] = async <T,>(path: string, init?: RequestInit) => {
      if (path.startsWith("/api/products/batches")) return [batch] as T;
      if (path.startsWith("/api/products/")) return [product] as T;
      if (path.startsWith("/api/customers/")) return [] as Customer[] as T;
      if (path === "/api/sales/") {
        salePayloads.push(JSON.parse(String(init?.body)));
        if (salePayloads.length === 1) throw new Error("network_unavailable");
        return invoice as T;
      }
      throw new Error(`Unexpected request: ${path}`);
    };

    const user = userEvent.setup();
    renderWithProviders(<PosPage shift={shift} />, { request });
    await user.click(await screen.findByRole("button", { name: "Add box" }));
    const submit = screen.getByRole("button", { name: "Complete sale" });
    await user.click(submit);
    await waitFor(() => expect(submit).toBeEnabled());
    await user.click(submit);

    expect(await screen.findByText("Invoice recorded successfully")).toBeInTheDocument();
    expect(salePayloads).toHaveLength(2);
    expect(salePayloads[0].idempotency_key).toBe(salePayloads[1].idempotency_key);
  });

  it("adds repeated scans without requiring Enter", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    const paths: string[] = [];
    const request: AuthContextValue["request"] = async <T,>(path: string) => {
      paths.push(path);
      if (path.startsWith("/api/products/batches")) return [batch] as T;
      if (path.startsWith("/api/products/search")) return [product] as T;
      if (path.startsWith("/api/products/")) return [] as Product[] as T;
      if (path.startsWith("/api/customers/")) return [] as Customer[] as T;
      throw new Error(`Unexpected request: ${path}`);
    };

    const user = userEvent.setup();
    renderWithProviders(<PosPage shift={shift} />, { request });
    const search = await screen.findByRole("searchbox", { name: "Scan a barcode or search by product name..." });

    await user.type(search, product.barcode!);
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: "Quantity" })).toHaveValue(1));
    expect(paths).toContain(`/api/products/search?q=${product.barcode}&limit=20`);
    await waitFor(() => expect(search).toHaveValue(""));
    await user.type(search, product.barcode!);
    await waitFor(() => expect(screen.getByRole("spinbutton", { name: "Quantity" })).toHaveValue(2));
  });
});
