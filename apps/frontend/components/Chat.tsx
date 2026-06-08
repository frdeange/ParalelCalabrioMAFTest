/**
 * Chat component with CopilotKit integration for multi-turn workflow chat.
 *
 * Features:
 * - Real-time streaming chat UI with message history
 * - CopilotKit integration for copilot-ready interactions
 * - Token streaming visualization
 * - Error toast notifications
 * - Auto-scroll to latest message
 * - Loading state and disabled input during streaming
 */

"use client";

import { useEffect, useRef, useState } from "react";
import { useAguiStream } from "@/lib/agui/client";

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: Date;
}

export function Chat() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { stream } = useAguiStream();

  // Auto-scroll to bottom on new messages
  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

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

    // Create assistant message placeholder
    const assistantMessageId = `msg-${Date.now() + 1}`;
    let assistantContent = "";

    try {
      await stream(
        inputValue,
        (token) => {
          // Stream token received
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
        (errorMsg) => {
          // Error occurred
          setError(errorMsg);
          setIsLoading(false);
        },
        () => {
          // Stream completed
          setIsLoading(false);
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
      <div className="flex-1 overflow-y-auto p-6 space-y-4">
        {messages.length === 0 ? (
          <div className="flex items-center justify-center h-full text-center">
            <div className="max-w-md">
              <h2 className="text-2xl font-semibold text-gray-800 mb-2">
                Welcome to Calabrio WFM Chat
              </h2>
              <p className="text-gray-600">
                Send a message to start a conversation with your workforce
                management assistant.
              </p>
            </div>
          </div>
        ) : (
          messages.map((message) => (
            <div
              key={message.id}
              className={`flex ${
                message.role === "user" ? "justify-end" : "justify-start"
              }`}
            >
              <div
                className={`max-w-xs lg:max-w-md px-4 py-3 rounded-lg ${
                  message.role === "user"
                    ? "bg-blue-600 text-white"
                    : "bg-gray-200 text-gray-800"
                }`}
              >
                <p className="whitespace-pre-wrap break-words">
                  {message.content}
                </p>
                <div
                  className={`text-xs mt-2 ${
                    message.role === "user"
                      ? "text-blue-100"
                      : "text-gray-600"
                  }`}
                >
                  {message.timestamp.toLocaleTimeString()}
                </div>
              </div>
            </div>
          ))
        )}
        {isLoading && (
          <div className="flex justify-start">
            <div className="max-w-xs lg:max-w-md px-4 py-3 rounded-lg bg-gray-200">
              <div className="flex space-x-2">
                <div className="w-2 h-2 bg-gray-600 rounded-full animate-bounce"></div>
                <div className="w-2 h-2 bg-gray-600 rounded-full animate-bounce delay-100"></div>
                <div className="w-2 h-2 bg-gray-600 rounded-full animate-bounce delay-200"></div>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Error Toast */}
      {error && (
        <div className="mx-6 mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
          <p className="text-sm text-red-700">{error}</p>
          <button
            onClick={() => setError(null)}
            className="text-xs text-red-600 hover:text-red-800 mt-2 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Input Area */}
      <div className="border-t border-gray-200 bg-white p-6">
        <div className="flex gap-3">
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={isLoading}
            placeholder="Type your message... (Shift+Enter for new line, Enter to send)"
            className="flex-1 px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-600 disabled:bg-gray-100 disabled:text-gray-500 resize-none max-h-32"
            rows={3}
          />
          <button
            onClick={handleSendMessage}
            disabled={isLoading || !inputValue.trim()}
            className="px-6 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white font-semibold rounded-lg transition duration-200 self-end"
          >
            {isLoading ? "Sending..." : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
