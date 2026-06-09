import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BU_DEBUG_HEADER,
  BU_STORAGE_KEY,
  getPocBuOptions,
  getSelectedBu,
  getServerSelectedBu,
  isPocBuOverrideEnabled,
  setSelectedBu,
  subscribeSelectedBu,
} from "@/lib/bu";

describe("constants", () => {
  it("exposes the localStorage key and the debug header name", () => {
    expect(BU_STORAGE_KEY).toBe("wfm.debug-bu");
    expect(BU_DEBUG_HEADER).toBe("x-debug-bu");
  });
});

describe("isPocBuOverrideEnabled", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is true only when the env flag is exactly 'true'", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OVERRIDE", "true");
    expect(isPocBuOverrideEnabled()).toBe(true);
  });

  it("is false when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OVERRIDE", "");
    expect(isPocBuOverrideEnabled()).toBe(false);
  });

  it("is false for any non-'true' value", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OVERRIDE", "1");
    expect(isPocBuOverrideEnabled()).toBe(false);
  });
});

describe("getPocBuOptions", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("defaults to 1,42,100 when unset", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OPTIONS", undefined);
    expect(getPocBuOptions()).toEqual(["1", "42", "100"]);
  });

  it("parses, trims, and keeps only numeric ids", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OPTIONS", " 5 , 7 , abc , 9 ");
    expect(getPocBuOptions()).toEqual(["5", "7", "9"]);
  });

  it("de-duplicates ids preserving first-seen order", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OPTIONS", "3,3,1,3,1");
    expect(getPocBuOptions()).toEqual(["3", "1"]);
  });

  it("drops empty entries", () => {
    vi.stubEnv("NEXT_PUBLIC_POC_BU_OPTIONS", "1,,2,");
    expect(getPocBuOptions()).toEqual(["1", "2"]);
  });
});

describe("getSelectedBu / setSelectedBu", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("returns null when nothing is stored", () => {
    expect(getSelectedBu()).toBeNull();
  });

  it("persists and reads back a valid numeric id", () => {
    setSelectedBu("42");
    expect(window.localStorage.getItem(BU_STORAGE_KEY)).toBe("42");
    expect(getSelectedBu()).toBe("42");
  });

  it("clears the selection when passed null", () => {
    setSelectedBu("42");
    setSelectedBu(null);
    expect(window.localStorage.getItem(BU_STORAGE_KEY)).toBeNull();
    expect(getSelectedBu()).toBeNull();
  });

  it("ignores a non-numeric value and clears storage instead", () => {
    setSelectedBu("not-a-number");
    expect(window.localStorage.getItem(BU_STORAGE_KEY)).toBeNull();
    expect(getSelectedBu()).toBeNull();
  });

  it("returns null when a manually corrupted value is stored", () => {
    window.localStorage.setItem(BU_STORAGE_KEY, "hacked");
    expect(getSelectedBu()).toBeNull();
  });

  it("returns null when localStorage.getItem throws", () => {
    const spy = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(() => {
        throw new Error("denied");
      });
    expect(getSelectedBu()).toBeNull();
    spy.mockRestore();
  });

  it("does not throw when localStorage.setItem throws", () => {
    const spy = vi
      .spyOn(Storage.prototype, "setItem")
      .mockImplementation(() => {
        throw new Error("quota");
      });
    expect(() => setSelectedBu("7")).not.toThrow();
    spy.mockRestore();
  });
});

describe("subscribeSelectedBu", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("notifies in-tab listeners on setSelectedBu", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSelectedBu(listener);
    setSelectedBu("7");
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("stops notifying after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSelectedBu(listener);
    unsubscribe();
    setSelectedBu("7");
    expect(listener).not.toHaveBeenCalled();
  });

  it("notifies on a cross-tab storage event for the BU key", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSelectedBu(listener);
    window.dispatchEvent(
      new StorageEvent("storage", { key: BU_STORAGE_KEY }),
    );
    expect(listener).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it("ignores storage events for unrelated keys", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeSelectedBu(listener);
    window.dispatchEvent(
      new StorageEvent("storage", { key: "some-other-key" }),
    );
    expect(listener).not.toHaveBeenCalled();
    unsubscribe();
  });
});

describe("getServerSelectedBu", () => {
  it("always returns null (no persistence during SSR)", () => {
    expect(getServerSelectedBu()).toBeNull();
  });
});
