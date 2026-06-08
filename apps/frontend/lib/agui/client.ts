/**
 * AG-UI client for streaming chat messages to the backend MAF workflow.
 *
 * This client handles:
 * - SSE (Server-Sent Events) streaming from the APIM AG-UI endpoint
 * - MSAL JWT Bearer token attachment on every request
 * - Proper error handling and user-friendly error messages
 * - Multi-turn conversation support
 */

import { useAuth } from "@/lib/use-auth";

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface StreamEvent {
  type: "token" | "error" | "done";
  data: string;
}

/**
 * Hook to stream a user message to the AG-UI backend endpoint.
 *
 * Yields tokens in real-time via Server-Sent Events and attaches MSAL bearer token.
 */
export function useAguiStream() {
  const { acquireToken } = useAuth();

  const stream = async (
    message: string,
    onToken: (token: string) => void,
    onError: (error: string) => void,
    onDone: () => void
  ) => {
    try {
      // Acquire MSAL token with API scope
      const token = await acquireToken();
      if (!token) {
        onError("Failed to acquire authentication token. Please sign in again.");
        return;
      }

      const backendUrl =
        process.env.NEXT_PUBLIC_BACKEND_API_URL ||
        "http://localhost:8000";
      const endpoint = `${backendUrl}/agui`;

      // POST user message to start streaming
      const response = await fetch(endpoint, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          // Optional: Include BU ID header (for multi-tenant routing)
          // "x-bu-id": businessUnitId,
        },
        body: JSON.stringify({
          messages: [{ role: "user", content: message }],
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        onError(
          `Backend error (${response.status}): ${errorText || response.statusText}`
        );
        return;
      }

      // Stream SSE events
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

        // Process complete lines
        for (let i = 0; i < lines.length - 1; i++) {
          const line = lines[i].trim();
          if (!line) continue;

          if (line.startsWith("data: ")) {
            const data = line.slice(6);
            try {
              const event = JSON.parse(data) as StreamEvent;
              if (event.type === "token") {
                onToken(event.data);
              } else if (event.type === "error") {
                onError(event.data);
              }
            } catch {
              // Silently skip malformed JSON
            }
          }
        }

        // Keep incomplete line in buffer
        buffer = lines[lines.length - 1];
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Unknown error";
      onError(`Network error: ${message}`);
    }
  };

  return { stream };
}
