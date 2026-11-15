import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

beforeEach(() => {
  window.sessionStorage.clear();
  // No test may reach the network: a real fetch call fails the test.
  vi.stubGlobal(
    "fetch",
    vi.fn(() => Promise.reject(new Error("tests must not call the real fetch"))),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
