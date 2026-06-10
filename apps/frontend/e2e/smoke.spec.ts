import { test, expect } from "@playwright/test";
import { seedAuthenticatedSession } from "./fixtures/auth";

/**
 * Smoke e2e (issue #30): happy path login → ask a question → see streamed reply.
 *
 * No real network is used:
 *  - MSAL is bypassed by seeding the sessionStorage token cache (see
 *    ./fixtures/auth.ts) so the app boots authenticated — no Entra calls.
 *  - The backend AG-UI endpoint is mocked with a canned SSE stream via
 *    page.route, so no backend/APIM is required.
 */

const ASSISTANT_TOKENS = ["Calabrio", " WFM", " is ", "ready."];
const ASSISTANT_REPLY = ASSISTANT_TOKENS.join("");

/** Build a canned AG-UI SSE body the frontend client knows how to parse. */
function aguiSseBody(): string {
  const frame = (obj: unknown) => `data: ${JSON.stringify(obj)}\n\n`;
  return (
    frame({ type: "RUN_STARTED", threadId: "e2e-thread", runId: "e2e-run" }) +
    ASSISTANT_TOKENS.map((delta) =>
      frame({ type: "TEXT_MESSAGE_CONTENT", messageId: "m1", delta })
    ).join("") +
    frame({ type: "RUN_FINISHED", threadId: "e2e-thread", runId: "e2e-run" })
  );
}

test("login → ask a question → see streamed reply", async ({ page }) => {
  // Record (and block) any attempt to reach real Entra ID endpoints so we can
  // assert the authenticated session is served entirely from the seeded cache.
  const entraCalls: string[] = [];
  await page.route(/login\.(microsoftonline\.com|windows\.net)/, (route) => {
    entraCalls.push(route.request().url());
    return route.abort();
  });

  // Mock the backend AG-UI streaming endpoint.
  let aguiRequestBody: unknown = null;
  await page.route("**/agui", async (route) => {
    const request = route.request();
    expect(request.method()).toBe("POST");
    aguiRequestBody = request.postDataJSON();
    // The MSAL access token we seeded must be forwarded as a Bearer header.
    expect(request.headers()["authorization"]).toContain("Bearer ");
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: aguiSseBody(),
    });
  });

  await seedAuthenticatedSession(page);

  // Start at the login page; an authenticated session must redirect to /chat.
  await page.goto("/login");
  await expect(page).toHaveURL(/\/chat$/);
  await expect(
    page.getByRole("heading", { name: /welcome to calabrio wfm chat/i })
  ).toBeVisible();

  // Ask a question.
  const input = page.getByPlaceholder(/type your message/i);
  await input.fill("Is the system ready?");
  await page.getByRole("button", { name: /send/i }).click();

  // The user's message is echoed in the transcript...
  await expect(page.getByText("Is the system ready?")).toBeVisible();
  // ...and the streamed assistant reply is rendered.
  await expect(page.getByText(ASSISTANT_REPLY)).toBeVisible();

  // The client sent the conversation history to the backend.
  expect(aguiRequestBody).toMatchObject({
    thread_id: expect.any(String),
    messages: expect.arrayContaining([
      expect.objectContaining({ role: "user", content: "Is the system ready?" }),
    ]),
  });

  // The whole flow was served from the seeded cache — no real Entra calls.
  expect(entraCalls).toEqual([]);
});
