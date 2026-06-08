/**
 * AG-UI client for streaming chat messages to the backend MAF workflow.
 *
 * Speaks the real AG-UI SSE protocol exposed by the backend
 * (`add_agent_framework_fastapi_endpoint`). Each SSE frame is a
 * `data: {json}` block whose `type` discriminator is one of the
 * AG-UI event names (RUN_STARTED, TEXT_MESSAGE_CONTENT, RUN_ERROR,
 * RUN_FINISHED, …). Field names on the wire are camelCase
 * (`messageId`, `delta`, `threadId`, `runId`).
 *
 * Responsibilities:
 * - SSE streaming from the APIM `chat-api-*` AG-UI route
 * - MSAL JWT Bearer token attachment on every request
 * - Mapping AG-UI events to simple token/error/done callbacks
 * - Multi-turn conversation via a stable `thread_id`
 */

import { useAuth } from "@/lib/use-auth";
import {
  BU_DEBUG_HEADER,
  getSelectedBu,
  isPocBuOverrideEnabled,
} from "@/lib/bu";

export interface AguiMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

/** Subset of AG-UI events this client reacts to. Extra fields are ignored. */
interface AguiEvent {
  type: string;
  delta?: string;
  message?: string;
}

export interface StreamParams {
  /** Full conversation history (each message needs a stable id). */
  messages: AguiMessage[];
  /** Stable thread id so the backend can load/append multi-turn history. */
  threadId: string;
}

export interface StreamHandlers {
  onToken: (token: string) => void;
  onError: (error: string) => void;
  onDone: () => void;
}

/**
 * Hook to stream a conversation turn to the AG-UI backend endpoint.
 *
 * Yields assistant tokens in real-time via Server-Sent Events and attaches
 * the MSAL bearer token acquired for the configured API scope.
 */
export function useAguiStream() {
  const { acquireToken } = useAuth();

  const stream = async (
    { messages, threadId }: StreamParams,
    { onToken, onError, onDone }: StreamHandlers
  ) => {
    try {
      // Acquire MSAL token with API scope
      const token = await acquireToken();
      if (!token) {
        onError("Failed to acquire authentication token. Please sign in again.");
        return;
      }

      const backendUrl =
        process.env.NEXT_PUBLIC_BACKEND_API_URL || "http://localhost:8000";
      const endpoint = `${backendUrl}/agui`;

      const headers: Record<string, string> = {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      };

      // POC mode: forward the operator-selected BU as the x-debug-bu header,
      // which APIM uses as the L3 layer of BU resolution (PLAN.md §6.4).
      if (isPocBuOverrideEnabled()) {
        const debugBu = getSelectedBu();
        if (debugBu) headers[BU_DEBUG_HEADER] = debugBu;
      }

      // Request body matches the backend AGUIRequest schema (see
      // apps/backend/tests/test_agui.py): messages carry an id, plus
      // thread_id / run_id (snake_case) for multi-turn continuity.
      const response = await fetch(endpoint, {
        method: "POST",
        headers,
        body: JSON.stringify({
          messages,
          thread_id: threadId,
          run_id:
            typeof crypto !== "undefined" && "randomUUID" in crypto
              ? crypto.randomUUID()
              : `run-${Date.now()}`,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        onError(
          `Backend error (${response.status}): ${errorText || response.statusText}`
        );
        return;
      }

      const reader = response.body?.getReader();
      if (!reader) {
        onError("Failed to read response stream");
        return;
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          onDone();
          break;
        }

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");

        // Process every complete line; keep the trailing partial in the buffer.
        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i].trim();
          if (!line || !line.startsWith("data:")) continue;

          const data = line.slice("data:".length).trim();
          if (!data) continue;

          let event: AguiEvent;
          try {
            event = JSON.parse(data) as AguiEvent;
          } catch {
            // Skip malformed / non-JSON frames (e.g. heartbeats)
            continue;
          }

          switch (event.type) {
            case "TEXT_MESSAGE_CONTENT":
              if (event.delta) onToken(event.delta);
              break;
            case "RUN_ERROR":
              onError(event.message || "The workflow reported an error.");
              break;
            case "RUN_FINISHED":
              onDone();
              return;
            // RUN_STARTED, TEXT_MESSAGE_START/END and others are
            // lifecycle markers with no UI payload here — ignore.
          }
        }

        buffer = lines[lines.length - 1];
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      onError(`Network error: ${message}`);
    }
  };

  return { stream };
}
