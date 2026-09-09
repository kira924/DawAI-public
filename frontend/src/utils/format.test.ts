import { describe, expect, it } from "vitest";

import { lineTotal } from "./format";

describe("lineTotal", () => {
  it("keeps two-decimal display arithmetic stable", () => {
    expect(lineTotal("19.99", 3)).toBe(59.97);
  });
});
