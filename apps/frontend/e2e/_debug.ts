import { chromium } from "@playwright/test";
import { buildMsalCacheEntries } from "./fixtures/auth";

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const entries = buildMsalCacheEntries();
  console.log("=== SEEDED KEYS ===");
  for (const k of Object.keys(entries)) console.log(k);

  await page.addInitScript((data: Record<string, string>) => {
    for (const [key, value] of Object.entries(data)) {
      window.sessionStorage.setItem(key, value);
    }
  }, entries);

  page.on("console", (msg) => console.log("PAGE LOG:", msg.text()));

  await page.goto("http://127.0.0.1:3100/login");
  await page.waitForTimeout(4000);

  const info = await page.evaluate(() => {
    const out: Record<string, string> = {};
    for (let i = 0; i < window.sessionStorage.length; i++) {
      const k = window.sessionStorage.key(i)!;
      out[k] = window.sessionStorage.getItem(k)!.slice(0, 80);
    }
    return { url: window.location.href, storage: out };
  });
  console.log("=== FINAL URL ===", info.url);
  console.log("=== SESSION STORAGE KEYS ===");
  for (const k of Object.keys(info.storage)) console.log(k, "=>", info.storage[k]);

  await browser.close();
})();
