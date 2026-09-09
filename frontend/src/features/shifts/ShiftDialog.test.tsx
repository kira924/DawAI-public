import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Shift } from "../../api/types";
import type { AuthContextValue } from "../../auth/context";
import { renderWithProviders } from "../../test/render";
import { ShiftDialog } from "./ShiftDialog";

const openedShift: Shift = {
  id: 20,
  status: "OPEN",
  start_time: "2026-09-05T08:00:00Z",
  end_time: null,
  opening_balance: "250.00",
  expected_closing_balance: "0.00",
  actual_closing_balance: null,
  difference: null,
  user_id: 4,
  tenant_id: 2,
  opening_business_date: "2026-09-05",
  closing_business_date: null,
};

describe("ShiftDialog", () => {
  it("opens a shift with an exact two-decimal balance", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    let submittedBody = "";
    const request: AuthContextValue["request"] = async <T,>(_path: string, init?: RequestInit) => {
      submittedBody = String(init?.body);
      return openedShift as T;
    };
    const onCompleted = vi.fn();
    const user = userEvent.setup();
    renderWithProviders(<ShiftDialog mode="open" onCompleted={onCompleted} />, { request });

    const balance = screen.getByLabelText("Opening cash balance");
    await user.clear(balance);
    await user.type(balance, "250");
    await user.click(screen.getByRole("button", { name: "Open shift" }));

    expect(JSON.parse(submittedBody)).toEqual({ opening_balance: "250.00" });
    expect(onCompleted).toHaveBeenCalledWith(openedShift);
  });
});
