import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, normalizeApiError, requestJson } from "./client";

describe("requestJson", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("adds bearer authorization and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(requestJson<{ status: string }>("/api/test", {}, "access-token")).resolves.toEqual({ status: "ok" });
    const requestInit = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(new Headers(requestInit.headers).get("Authorization")).toBe("Bearer access-token");
  });

  it("returns a typed API error without exposing non-JSON bodies", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("unavailable", { status: 503 })));

    await expect(requestJson("/api/test")).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
    });
  });
});

describe("normalizeApiError", () => {
  it("combines validation messages", () => {
    const error = new ApiError(422, [{ msg: "Quantity must be positive" }, { msg: "Price is invalid" }]);
    expect(normalizeApiError(error)).toBe("Quantity must be positive. Price is invalid");
  });
});
