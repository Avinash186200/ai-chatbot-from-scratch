import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { FORECAST_ENVELOPE, json, mockFetch, sseBody } from "./helpers";

const THREAD = { thread_id: "thread-0001", user: "local", title: "Forecast RATE_LONG", created_at: "c", updated_at: "u" };
const CONFIG = { agent: "graph", llm_provider: "scripted", llm_model: "", version: "0.3.0" };

function connect(token = "tok-1234") {
  fireEvent.change(screen.getByLabelText("Token"), { target: { value: token } });
  fireEvent.click(screen.getByRole("button", { name: "Connect" }));
}

function send(text: string) {
  fireEvent.change(screen.getByLabelText("Message"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("App", () => {
  it("asks for a token first and keeps it out of localStorage", async () => {
    const { fetch } = mockFetch({ "GET /api/threads": () => json({ threads: [] }), "GET /api/config": () => json(CONFIG) });
    render(<App fetchImpl={fetch} />);
    expect(screen.getByRole("form", { name: "Connect" })).toBeTruthy();
    connect();
    await screen.findByText("No conversations yet.");
    expect(window.sessionStorage.getItem("aa.token")).toBe("tok-1234");
    expect(window.localStorage.getItem("aa.token")).toBeNull();
    expect((await screen.findByTestId("server-config")).textContent).toContain("agent: graph");
  });

  it("streams a run: tool call with arguments and result, answer, provenance", async () => {
    const { fetch } = mockFetch({
      "GET /api/threads": () => json({ threads: [THREAD] }),
      "GET /api/config": () => json(CONFIG),
      "POST /api/chat": () =>
        new Response(
          sseBody([
            { type: "run.started", data: { message: "q" } },
            { type: "route.decided", data: { route: "tool", source: "rules", confidence: 0.9 } },
            { type: "tool.called", data: { id: "c1", name: "forecast_series", arguments: { series: "RATE_LONG", horizon: 5 } } },
            { type: "tool.result", data: { id: "c1", name: "forecast_series", ok: true, envelope: FORECAST_ENVELOPE } },
            { type: "message.completed", data: { content: "RATE_LONG answer. As of 2023-06-30, snapshot snap-1a2b3c4d." } },
            { type: "run.finished", data: { status: "completed" } },
          ]),
          { headers: { "X-Thread-Id": "thread-0001" } },
        ),
    });
    render(<App fetchImpl={fetch} />);
    connect();
    await screen.findByText("Forecast RATE_LONG");
    send("Forecast RATE_LONG 5 days ahead");

    const card = await screen.findByRole("article", { name: "Tool call forecast_series" });
    expect(within(card).getByText("ok")).toBeTruthy();
    expect(card.textContent).toContain('"horizon": 5');
    expect(card.textContent).toContain('"snapshot_id": "snap-1a2b3c4d"');
    expect((await screen.findByTestId("assistant-text")).textContent).toContain("As of 2023-06-30");
    expect(screen.getByText("route: tool (rules)")).toBeTruthy();

    const panel = screen.getByRole("complementary", { name: "Provenance" });
    for (const value of ["forecast_series", "ar1-placeholder", "1.1.0", "2023-06-30", "snap-1a2b3c4d"]) {
      expect(within(panel).getByText(value)).toBeTruthy();
    }
  });

  it("renders model output as text, never as HTML", async () => {
    const payload = '<img src=x onerror="alert(1)"><b>bold</b><script>window.__pwned=1</script>';
    const { fetch } = mockFetch({
      "GET /api/threads": () => json({ threads: [] }),
      "GET /api/config": () => json(CONFIG),
      "POST /api/chat": () =>
        new Response(sseBody([{ type: "message.completed", data: { content: payload } }, { type: "run.finished", data: {} }])),
    });
    const { container } = render(<App fetchImpl={fetch} />);
    connect();
    await screen.findByText("No conversations yet.");
    send("hi");
    const text = await screen.findByTestId("assistant-text");
    expect(text.textContent).toBe(payload);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it("creates a conversation and resumes an existing one with its tool calls", async () => {
    const { fetch, calls } = mockFetch({
      "GET /api/threads": () => json({ threads: [THREAD] }),
      "GET /api/config": () => json(CONFIG),
      "POST /api/threads": () => json({ ...THREAD, thread_id: "thread-0002", title: "New conversation" }, 201),
      "GET /api/threads/thread-0001": () =>
        json({
          ...THREAD,
          messages: [
            { role: "user", content: "Forecast RATE_LONG" },
            { role: "tool", id: "c1", name: "forecast_series", arguments: { horizon: 5 }, envelope: FORECAST_ENVELOPE },
            { role: "assistant", content: "Earlier answer." },
          ],
        }),
    });
    render(<App fetchImpl={fetch} />);
    connect();
    fireEvent.click(await screen.findByRole("button", { name: "Forecast RATE_LONG" }));
    expect((await screen.findByTestId("assistant-text")).textContent).toBe("Earlier answer.");
    expect(screen.getByRole("article", { name: "Tool call forecast_series" })).toBeTruthy();
    expect(within(screen.getByRole("complementary", { name: "Provenance" })).getByText("snap-1a2b3c4d")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    await waitFor(() => expect(calls.some((c) => c.init.method === "POST" && c.url === "/api/threads")).toBe(true));
    await waitFor(() => expect(screen.queryByTestId("assistant-text")).toBeNull());
  });

  it("drops a rejected token and shows why", async () => {
    const { fetch } = mockFetch({
      "GET /api/threads": () => json({ detail: "Invalid or missing bearer token" }, 401),
      "GET /api/config": () => json({}, 401),
    });
    render(<App fetchImpl={fetch} />);
    connect("wrong-token");
    expect((await screen.findByRole("status")).textContent).toBe("The server rejected the token.");
    expect(screen.getByRole("form", { name: "Connect" })).toBeTruthy();
    expect(window.sessionStorage.getItem("aa.token")).toBeNull();
  });

  it("shows a refused tool call and a failed run", async () => {
    const { fetch } = mockFetch({
      "GET /api/threads": () => json({ threads: [] }),
      "GET /api/config": () => json(CONFIG),
      "POST /api/chat": () =>
        new Response(
          sseBody([
            { type: "tool.called", data: { id: "c1", name: "forecast_series", arguments: { horizon: 50 } } },
            { type: "tool.result", data: { id: "c1", ok: false, envelope: { ok: false, payload: { error: { code: "UNSUPPORTED_HORIZON", message: "horizon must be between 1 and 20" } } } } },
            { type: "run.failed", data: { error: "stopped after 6 steps" } },
          ]),
        ),
    });
    render(<App fetchImpl={fetch} />);
    connect();
    await screen.findByText("No conversations yet.");
    send("Forecast 50 days");
    expect(await screen.findByText("refused: UNSUPPORTED_HORIZON")).toBeTruthy();
    expect(screen.getByText("horizon must be between 1 and 20")).toBeTruthy();
    expect((await screen.findByRole("alert")).textContent).toBe("stopped after 6 steps");
    expect(within(screen.getByRole("complementary", { name: "Provenance" })).queryByRole("table")).toBeNull();
  });
});
