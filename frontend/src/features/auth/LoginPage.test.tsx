import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { renderWithProviders } from "../../test/render";
import { LoginPage } from "./LoginPage";

describe("LoginPage", () => {
  it("submits the entered credentials", async () => {
    window.localStorage.setItem("dawai.locale", "en");
    const login = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderWithProviders(<LoginPage />, { state: "anonymous", user: null, login });

    await user.type(screen.getByLabelText("Email address"), "manager@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(login).toHaveBeenCalledWith("manager@example.test", "correct horse battery staple");
  });
});
