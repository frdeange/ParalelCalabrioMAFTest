/**
 * Multi-turn chat component wired to the backend MAF workflow over the
 * AG-UI SSE protocol (via `useAguiStream`).
 *
 * Visual design follows the Calabrio "Supervisor Assist" reference
 * (docs/referenceImages): lavender user bubbles, markdown-rendered
 * assistant replies (tables included), a light-blue input band with a
 * circular send button, and a live agent-progress indicator that
 * reflects which workflow executor is currently running.
 */

"use client";

import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAguiStream, type AguiMessage } from "@/lib/agui/client";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

/**
 * Maps backend workflow executor ids (AG-UI ``STEP_STARTED`` ``stepName``)
 * to the user-facing progress message shown while that agent runs.
 */
const AGENT_PROGRESS: Record<string, string> = {
  intent_step: "Understanding your request",
  sql_builder_step: "Generating database request",
  query_executor_step: "Almost finished responding",
};

export interface ChatProps {
  /** Display name used in the welcome heading ("Hello, <name>!"). */
  userName?: string;
}

export function Chat({ userName }: ChatProps = {}) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Friendly progress message for the agent currently running, or null
  // when idle / once the assistant has started streaming its answer.
  const [progress, setProgress] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Stable thread id for the whole session so the backend can persist
  // and reload multi-turn history (Cosmos chat-history). Generated lazily
  // on first send (in an event handler, not during render) to keep the
  // render pure.
  const threadIdRef = useRef<string | null>(null);
  const getThreadId = () => {
    if (threadIdRef.current === null) {
      threadIdRef.current =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `thread-${Date.now()}`;
    }
    return threadIdRef.current;
  };
  const { stream } = useAguiStream();

  // Auto-scroll to bottom on new messages / progress changes
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, progress]);

  const handleSendMessage = async () => {
    if (!inputValue.trim()) return;

    // Clear any previous errors
    setError(null);

    // Add user message immediately
    const userMessage: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: "user",
      content: inputValue,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, userMessage]);
    setInputValue("");
    setIsLoading(true);
    setProgress(AGENT_PROGRESS.intent_step);

    // Build the full history (with stable ids) the backend expects.
    const history: AguiMessage[] = [...messages, userMessage].map((m) => ({
      id: m.id,
      role: m.role,
      content: m.content,
    }));

    // Create assistant message placeholder
    const assistantMessageId = `msg-${Date.now() + 1}`;
    let assistantContent = "";

    try {
      await stream(
        { messages: history, threadId: getThreadId() },
        {
          onStep: (stepName) => {
            // Surface the friendly phase message for known executors;
            // ignore unknown / superstep markers.
            const label = AGENT_PROGRESS[stepName];
            if (label) setProgress(label);
          },
          onToken: (token) => {
            // First token means the assistant is answering — drop the
            // progress indicator so the reply takes over.
            setProgress(null);
            assistantContent += token;
            setMessages((prev) => {
              const lastMsg = prev[prev.length - 1];
              if (lastMsg?.id === assistantMessageId) {
                return [
                  ...prev.slice(0, -1),
                  { ...lastMsg, content: assistantContent },
                ];
              }
              return [
                ...prev,
                {
                  id: assistantMessageId,
                  role: "assistant",
                  content: assistantContent,
                  timestamp: new Date(),
                },
              ];
            });
          },
          onError: (errorMsg) => {
            // Error occurred
            setError(errorMsg);
            setProgress(null);
            setIsLoading(false);
          },
          onDone: () => {
            // Stream completed
            setProgress(null);
            setIsLoading(false);
          },
        }
      );

      // Ensure assistant message is in the list (in case no tokens were streamed)
      setMessages((prev) => {
        if (!prev.find((m) => m.id === assistantMessageId)) {
          return [
            ...prev,
            {
              id: assistantMessageId,
              role: "assistant",
              content:
                assistantContent || "(No response from assistant)",
              timestamp: new Date(),
            },
          ];
        }
        return prev;
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      setError(`Failed to send message: ${message}`);
      setProgress(null);
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && !isLoading) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Chat Messages Container */}
      <div className="flex-1 overflow-y-auto px-4 py-6 md:px-10">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full text-center">
            <div className="max-w-md">
              <h2 className="text-3xl font-bold text-slate-800 mb-3">
                Hello{userName ? `, ${userName}` : ""}!
              </h2>
              <p className="text-slate-500 text-lg">
                What can I help you with today?
              </p>
            </div>
          </div>
        ) : (
          <div className="mx-auto w-full max-w-4xl space-y-5">
            {messages.map((message) =>
              message.role === "user" ? (
                <div key={message.id} className="flex justify-end">
                  <div className="max-w-[80%]">
                    <div className="rounded-2xl rounded-tr-sm bg-[var(--calabrio-user-bubble)] px-4 py-3 text-slate-800">
                      <p className="whitespace-pre-wrap break-words">
                        {message.content}
                      </p>
                    </div>
                    <div className="mt-1 text-right text-xs text-slate-400">
                      {message.timestamp.toLocaleTimeString()}
                    </div>
                  </div>
                </div>
              ) : (
                <div key={message.id} className="flex justify-start">
                  <div className="w-full max-w-[95%]">
                    <div className="rounded-2xl rounded-tl-sm bg-[var(--calabrio-assistant-bubble)] px-4 py-3 text-slate-800">
                      <div className="markdown-body">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                          {message.content}
                        </ReactMarkdown>
                      </div>
                    </div>
                    <div className="mt-1 text-xs text-slate-400">
                      {message.timestamp.toLocaleTimeString()}
                    </div>
                  </div>
                </div>
              )
            )}

            {/* Agent progress indicator */}
            {progress && (
              <div className="flex justify-start" aria-live="polite">
                <div className="flex items-center gap-3 rounded-2xl rounded-tl-sm bg-[var(--calabrio-assistant-bubble)] px-4 py-3">
                  <span className="flex gap-1">
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--calabrio-blue)]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--calabrio-blue)] [animation-delay:0.15s]" />
                    <span className="h-2 w-2 animate-bounce rounded-full bg-[var(--calabrio-blue)] [animation-delay:0.3s]" />
                  </span>
                  <span className="text-sm font-medium text-slate-600">
                    {progress}
                  </span>
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>
        )}
      </div>

      {/* Error Toast */}
      {error && (
        <div className="mx-auto mb-4 w-full max-w-4xl rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={() => setError(null)}
            className="mt-2 text-xs text-red-600 underline hover:text-red-800"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Input Area — light-blue band per the Calabrio reference */}
      <div className="bg-[var(--calabrio-input-band)] px-4 py-4 md:px-10">
        <div className="mx-auto flex w-full max-w-4xl items-end gap-3">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Type your message... (Shift+Enter for new line, Enter to send)"
            className="max-h-32 flex-1 resize-none rounded-xl border border-white/70 bg-white px-4 py-3 text-slate-800 shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--calabrio-blue)] disabled:bg-gray-100 disabled:text-gray-500"
            rows={1}
          />
          <button
            onClick={handleSendMessage}
            disabled={isLoading || !inputValue.trim()}
            aria-label="Send"
            title="Send"
            className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl bg-[var(--calabrio-blue)] text-white transition duration-200 hover:bg-[var(--calabrio-blue-dark)] disabled:cursor-not-allowed disabled:bg-blue-300"
          >
            {isLoading ? (
              <svg
                className="h-5 w-5 animate-spin"
                viewBox="0 0 24 24"
                fill="none"
                aria-hidden="true"
              >
                <circle
                  className="opacity-25"
                  cx="12"
                  cy="12"
                  r="10"
                  stroke="currentColor"
                  strokeWidth="4"
                />
                <path
                  className="opacity-75"
                  fill="currentColor"
                  d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
                />
              </svg>
            ) : (
              <svg
                className="h-5 w-5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            )}
          </button>
        </div>
        <p className="mt-3 text-center text-xs text-slate-500">
          Supervisor assist is a generative AI-based solution and may make
          mistakes
        </p>
      </div>
    </div>
  );
}
