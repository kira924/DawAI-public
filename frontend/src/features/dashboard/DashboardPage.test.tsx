import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DashboardSummary } from "../../api/types";
import type { AuthContextValue } from "../../auth/context";
import { renderWithProviders } from "../../test/render";
import { DashboardPage } from "./DashboardPage";

describe("DashboardPage", () => {
  it("shows the Cairo business-day summary", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    const summary: DashboardSummary = {
      business_date: "2026-09-05",
      timezone: "Africa/Cairo",
      total_sales: "1500.00",
      total_expenses: "100.00",
      total_returns: "50.00",
      net_revenue: "1350.00",
      low_stock_count: 3,
    };
    const request: AuthContextValue["request"] = async <T,>() => summary as T;

    renderWithProviders(<DashboardPage />, { request });

    expect(await screen.findByText("Africa/Cairo")).toBeInTheDocument();
    expect(screen.getByText("Low-stock products")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
  });
});
