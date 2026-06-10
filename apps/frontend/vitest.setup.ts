import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount React trees and clear the jsdom DOM between tests so that
// component state (and rendered nodes) never leak across test cases.
afterEach(() => {
  cleanup();
});
