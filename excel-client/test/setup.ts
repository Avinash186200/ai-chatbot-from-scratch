// A stand-in for the CustomFunctions runtime global that office.js provides,
// and a guard that fails any test that reaches for the real network.

class FakeCustomFunctionsError extends Error {
  constructor(
    readonly code: string,
    message?: string,
  ) {
    super(message);
    this.name = "CustomFunctions.Error";
  }
}

(globalThis as Record<string, unknown>).CustomFunctions = {
  Error: FakeCustomFunctionsError,
  ErrorCode: { invalidValue: "#VALUE!", notAvailable: "#N/A" },
  associate: jest.fn(),
};

(globalThis as Record<string, unknown>).fetch = () => {
  throw new Error("tests must not use the real fetch; pass a mock to GatewayClient");
};
