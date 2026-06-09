import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

import { useAguiStream } from "@/lib/agui/client";

// --- Mocks --------------------------------------------------------------

const acquireToken = vi.fn();
vi.mock("@/lib/use-auth", () => ({
  useAuth: () => ({ acquireToken }),
}));

const buState = { enabled: false, selected: null as string | null };
vi.mock("@/lib/bu", () => ({
  BU_DEBUG_HEADER: "x-debug-bu",
  isPocBuOverrideEnabled: () => buState.enabled,
  getSelectedBu: () => buState.selected,
}));

/** Build a Response whose body streams the given SSE text as one chunk. */
function sseResponse(body: string, init?: ResponseInit): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status: 200, ...init });
}

function frame(event: Record<string, unknown>): string {
  return `data: ${JSON.stringify(event)}\n`;
}

beforeEach(() => {
  acquireToken.mockReset();
  buState.enabled = false;
  buState.selected = null;
  vi.stubEnv("NEXT_PUBLIC_BACKEND_API_URL", "https://apim.example/chat-api-dev");
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("useAguiStream", () => {
  const params = {
    messages: [{ id: "1", role: "user" as const, content: "hi" }],
    threadId: "thread-1",
  };

  it("errors out when no token can be acquired", async () => {
    acquireToken.mockResolvedValue(null);
    const onError = vi.fn();
    const fetchSpy = vi.spyOn(globalThis, "fetch");

    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError,
      onDone: vi.fn(),
    });

    expect(onError).toHaveBeenCalledWith(
      expect.stringContaining("Failed to acquire authentication token"),
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("streams tokens and completes on RUN_FINISHED", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    const body =
      frame({ type: "RUN_STARTED" }) +
      frame({ type: "TEXT_MESSAGE_CONTENT", delta: "Hello" }) +
      frame({ type: "TEXT_MESSAGE_CONTENT", delta: " world" }) +
      frame({ type: "RUN_FINISHED" });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(body));

    const onToken = vi.fn();
    const onDone = vi.fn();
    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, { onToken, onError: vi.fn(), onDone });

    expect(onToken).toHaveBeenNthCalledWith(1, "Hello");
    expect(onToken).toHaveBeenNthCalledWith(2, " world");
    expect(onDone).toHaveBeenCalledTimes(1);
  });

  it("attaches the bearer token and posts the AGUIRequest body", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(sseResponse(frame({ type: "RUN_FINISHED" })));

    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError: vi.fn(),
      onDone: vi.fn(),
    });

    const [url, init] = fetchSpy.mock.calls[0];
    expect(url).toBe("https://apim.example/chat-api-dev/agui");
    const headers = init?.headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer jwt-token");
    const sentBody = JSON.parse(init?.body as string);
    expect(sentBody.messages).toEqual(params.messages);
    expect(sentBody.thread_id).toBe("thread-1");
    expect(sentBody.run_id).toBeTruthy();
  });

  it("forwards x-debug-bu when POC mode is on and a BU is selected", async () => {
    buState.enabled = true;
    buState.selected = "42";
    acquireToken.mockResolvedValue("jwt-token");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(sseResponse(frame({ type: "RUN_FINISHED" })));

    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError: vi.fn(),
      onDone: vi.fn(),
    });

    const headers = fetchSpy.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers["x-debug-bu"]).toBe("42");
  });

  it("omits x-debug-bu when POC mode is off", async () => {
    buState.enabled = false;
    buState.selected = "42";
    acquireToken.mockResolvedValue("jwt-token");
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(sseResponse(frame({ type: "RUN_FINISHED" })));

    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError: vi.fn(),
      onDone: vi.fn(),
    });

    const headers = fetchSpy.mock.calls[0][1]?.headers as Record<string, string>;
    expect(headers["x-debug-bu"]).toBeUndefined();
  });

  it("reports a RUN_ERROR message", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      sseResponse(frame({ type: "RUN_ERROR", message: "boom" })),
    );

    const onError = vi.fn();
    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError,
      onDone: vi.fn(),
    });

    expect(onError).toHaveBeenCalledWith("boom");
  });

  it("surfaces a non-OK HTTP response as an error", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("nope", { status: 422, statusText: "Unprocessable" }),
    );

    const onError = vi.fn();
    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError,
      onDone: vi.fn(),
    });

    expect(onError).toHaveBeenCalledWith(expect.stringContaining("422"));
  });

  it("skips malformed SSE frames without throwing", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    const body =
      "data: not-json\n" +
      frame({ type: "TEXT_MESSAGE_CONTENT", delta: "ok" }) +
      frame({ type: "RUN_FINISHED" });
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(body));

    const onToken = vi.fn();
    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken,
      onError: vi.fn(),
      onDone: vi.fn(),
    });

    expect(onToken).toHaveBeenCalledExactlyOnceWith("ok");
  });

  it("wraps a thrown fetch as a network error", async () => {
    acquireToken.mockResolvedValue("jwt-token");
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));

    const onError = vi.fn();
    const { result } = renderHook(() => useAguiStream());
    await result.current.stream(params, {
      onToken: vi.fn(),
      onError,
      onDone: vi.fn(),
    });

    expect(onError).toHaveBeenCalledWith(expect.stringContaining("offline"));
  });
});
