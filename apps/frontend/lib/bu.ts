/**
 * Business Unit (BU) override helpers — POC mode only.
 *
 * In POC mode (`NEXT_PUBLIC_POC_BU_OVERRIDE=true`) the operator can pick a BU
 * from a dropdown. The selection is persisted in localStorage and forwarded as
 * the `x-debug-bu` header on every AG-UI request. APIM uses that header as the
 * L3 layer of its 4-layer BU resolution (PLAN.md §6.4 / D8):
 *   (1) JWT claim → (2) domain map → (3) x-debug-bu (POC) → (4) BU_ID_DEFAULT.
 *
 * Outside POC mode none of this is active and the header is never sent.
 */

/** localStorage key under which the selected BU id is persisted. */
export const BU_STORAGE_KEY = "wfm.debug-bu";

/** Header APIM reads as the L3 POC override. */
export const BU_DEBUG_HEADER = "x-debug-bu";

/** True when the POC BU-override feature is enabled via env. */
export function isPocBuOverrideEnabled(): boolean {
  return process.env.NEXT_PUBLIC_POC_BU_OVERRIDE === "true";
}

/**
 * BU ids selectable in POC mode. Configured via
 * `NEXT_PUBLIC_POC_BU_OPTIONS` (comma-separated) with a sensible default.
 *
 * Values must be numeric BU ids; non-numeric entries are dropped and
 * duplicates are removed so a misconfigured env can never feed an invalid
 * value into the `x-debug-bu` request header.
 */
export function getPocBuOptions(): string[] {
  const raw = process.env.NEXT_PUBLIC_POC_BU_OPTIONS ?? "1,42,100";
  const seen = new Set<string>();
  const options: string[] = [];
  for (const part of raw.split(",")) {
    const id = part.trim();
    if (isValidBuId(id) && !seen.has(id)) {
      seen.add(id);
      options.push(id);
    }
  }
  return options;
}

/** True when `id` is a non-empty string of digits (a valid BU id). */
function isValidBuId(id: string | null | undefined): id is string {
  return typeof id === "string" && /^\d+$/.test(id);
}

/**
 * Read the persisted BU id, or null when unset / invalid / not in a browser.
 * `localStorage` access is guarded because it can throw in some browser modes
 * (e.g. privacy settings), and a manually edited value is validated before use.
 */
export function getSelectedBu(): string | null {
  if (typeof window === "undefined") return null;
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(BU_STORAGE_KEY);
  } catch {
    return null;
  }
  return isValidBuId(stored) ? stored : null;
}

/**
 * Persist (or clear) the selected BU id. Only valid numeric ids are stored;
 * any other value clears the selection. `localStorage` access is guarded so a
 * throwing storage backend can't break selection.
 */
export function setSelectedBu(buId: string | null): void {
  if (typeof window === "undefined") return;
  try {
    if (isValidBuId(buId)) {
      window.localStorage.setItem(BU_STORAGE_KEY, buId);
    } else {
      window.localStorage.removeItem(BU_STORAGE_KEY);
    }
  } catch {
    // Storage unavailable (private mode, quota, disabled): keep going so the
    // in-tab listeners still fire and the UI stays responsive.
  }
  for (const listener of listeners) listener();
}

/** In-tab listeners notified whenever the selected BU changes. */
const listeners = new Set<() => void>();

/**
 * Subscribe to BU-selection changes. Designed for `useSyncExternalStore`:
 * fires on in-tab `setSelectedBu` calls and cross-tab `storage` events.
 * Returns an unsubscribe function.
 */
export function subscribeSelectedBu(onChange: () => void): () => void {
  listeners.add(onChange);
  const onStorage = (e: StorageEvent) => {
    if (e.key === BU_STORAGE_KEY) onChange();
  };
  if (typeof window !== "undefined") {
    window.addEventListener("storage", onStorage);
  }
  return () => {
    listeners.delete(onChange);
    if (typeof window !== "undefined") {
      window.removeEventListener("storage", onStorage);
    }
  };
}

/** Server snapshot for `useSyncExternalStore` (no persistence during SSR). */
export function getServerSelectedBu(): string | null {
  return null;
}
