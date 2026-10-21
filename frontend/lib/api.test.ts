import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, NetworkError } from "./api";

// Login-regression investigation: the login page's error handling depends on
// `request()` (lib/api.ts) throwing a distinguishable error for three cases —
// a real backend rejection (ApiError with the backend's detail message), a
// request that never reached the server at all (NetworkError, a subclass of
// ApiError so existing `instanceof ApiError` checks still catch it), and never
// an unhandled/uncaught exception that would crash the caller.

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.request error handling", () => {
  it("throws an ApiError carrying the backend's detail message on a non-2xx JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 401,
        ok: false,
        statusText: "Unauthorized",
        headers: { get: () => "application/json" },
        json: async () => ({ detail: "Invalid email or password" }),
      })
    );

    await expect(api.post("/api/v1/auth/login", { email: "a@b.com", password: "wrong" })).rejects.toMatchObject({
      status: 401,
      message: "Invalid email or password",
    });
  });

  it("throws a NetworkError (not a raw/uncaught exception) when the request never reaches the server", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new TypeError("Failed to fetch"))
    );

    let caught: unknown;
    try {
      await api.post("/api/v1/auth/login", { email: "a@b.com", password: "x" });
    } catch (err) {
      caught = err;
    }

    expect(caught).toBeInstanceOf(NetworkError);
    // NetworkError is a subclass of ApiError so existing `err instanceof ApiError`
    // call sites (login page, every ProtectedShell-wrapped page) keep working
    // unchanged and show NetworkError's message instead of a raw stack trace.
    expect(caught).toBeInstanceOf(ApiError);
    expect((caught as ApiError).status).toBe(0);
    expect((caught as ApiError).message).not.toMatch(/fetch|TypeError/i);
  });

  it("resolves normally on a 200 JSON response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 200,
        ok: true,
        headers: { get: () => "application/json" },
        json: async () => ({ access_token: "abc", token_type: "bearer" }),
      })
    );

    const result = await api.post<{ access_token: string }>("/api/v1/auth/login", {
      email: "a@b.com",
      password: "correct",
    });
    expect(result.access_token).toBe("abc");
  });
});
