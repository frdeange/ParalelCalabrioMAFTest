import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Chat } from "@/components/Chat";

// Controllable stream mock standing in for the AG-UI client.
type Handlers = {
  onToken: (t: string) => void;
  onError: (e: string) => void;
  onDone: () => void;
  onStep?: (stepName: string) => void;
};
const streamImpl = vi.fn();
vi.mock("@/lib/agui/client", () => ({
  useAguiStream: () => ({ stream: streamImpl }),
}));

// jsdom doesn't implement scrollIntoView; stub it and restore the original
// (if any) after each test so the mutation can't leak into other suites.
const originalScrollIntoView = Element.prototype.scrollIntoView;

beforeEach(() => {
  streamImpl.mockReset();
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.clearAllMocks();
  Element.prototype.scrollIntoView = originalScrollIntoView;
});

describe("Chat", () => {
  it("shows the welcome empty state initially", () => {
    render(<Chat />);
    expect(screen.getByText(/what can i help you with today/i)).toBeInTheDocument();
  });

  it("greets the user by name when provided", () => {
    render(<Chat userName="Alex" />);
    expect(screen.getByText(/hello, alex!/i)).toBeInTheDocument();
  });

  it("disables Send while the input is empty", () => {
    render(<Chat />);
    expect(screen.getByRole("button", { name: /send/i })).toBeDisabled();
  });

  it("renders the user message and streamed assistant tokens", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onToken("Hello");
        handlers.onToken(" there");
        handlers.onDone();
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(screen.getByText("hi")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText("Hello there")).toBeInTheDocument();
    });
    // Input cleared after sending.
    expect(screen.getByPlaceholderText(/type your message/i)).toHaveValue("");
  });

  it("shows a dismissable error toast on stream error", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onError("backend exploded");
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(await screen.findByText("backend exploded")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /dismiss/i }));
    expect(screen.queryByText("backend exploded")).not.toBeInTheDocument();
  });

  it("adds a placeholder assistant message when no tokens stream", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onDone();
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(
      await screen.findByText("(No response from assistant)"),
    ).toBeInTheDocument();
  });

  it("sends on Enter and passes message history to the stream", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onDone();
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(
      screen.getByPlaceholderText(/type your message/i),
      "first{Enter}",
    );

    await waitFor(() => expect(streamImpl).toHaveBeenCalledTimes(1));
    const [params] = streamImpl.mock.calls[0];
    expect(params.messages).toHaveLength(1);
    expect(params.messages[0]).toMatchObject({ role: "user", content: "first" });
    expect(params.threadId).toBeTruthy();
  });

  it("does not send when the input is only whitespace", async () => {
    const user = userEvent.setup();
    render(<Chat />);
    await user.type(
      screen.getByPlaceholderText(/type your message/i),
      "   {Enter}",
    );
    expect(streamImpl).not.toHaveBeenCalled();
  });

  it("shows the agent progress message while a step runs", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onStep?.("sql_builder_step");
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(
      await screen.findByText(/generating database request/i),
    ).toBeInTheDocument();
  });

  it("keeps query executor progress visible while tokens are streaming", async () => {
    streamImpl.mockImplementation(
      async (_params: unknown, handlers: Handlers) => {
        handlers.onStep?.("query_executor_step");
        handlers.onToken("partial");
      },
    );

    const user = userEvent.setup();
    render(<Chat />);
    await user.type(screen.getByPlaceholderText(/type your message/i), "hi");
    await user.click(screen.getByRole("button", { name: /send/i }));

    expect(
      await screen.findByText(/almost finished responding/i),
    ).toBeInTheDocument();
  });
});
