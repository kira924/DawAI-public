import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductBatch } from "../../api/types";
import { renderWithProviders } from "../../test/render";
import { ExpiryBadge } from "./ExpiryBadge";

describe("ExpiryBadge", () => {
  it("shows critical stock clearly", () => {
    window.localStorage.setItem("dawai.locale", "en");
    const batch: ProductBatch = {
      id: 1,
      batch_number: "B-001",
      expiry_date: "2026-09-25",
      quantity: 10,
      product_id: 1,
      tenant_id: 2,
      days_until_expiry: 20,
      expiry_status: "critical",
      is_sellable: true,
    };
    renderWithProviders(<ExpiryBadge batch={batch} />);
    expect(screen.getByText("Critical expiry")).toBeInTheDocument();
  });
});
