/**
 * POC-only Business Unit selector.
 *
 * Renders a dropdown that lets the operator override the BU during demos.
 * The selection is persisted in localStorage and picked up by the AG-UI
 * client, which forwards it as the `x-debug-bu` header on every request.
 *
 * Renders nothing when POC mode is off (`NEXT_PUBLIC_POC_BU_OVERRIDE` != "true").
 */

"use client";

import { useSyncExternalStore } from "react";
import {
  getPocBuOptions,
  getSelectedBu,
  getServerSelectedBu,
  isPocBuOverrideEnabled,
  setSelectedBu,
  subscribeSelectedBu,
} from "@/lib/bu";

export function BuSelector() {
  const enabled = isPocBuOverrideEnabled();
  const options = getPocBuOptions();

  // Read the persisted selection from localStorage via an external store so
  // the value stays in sync without setState-in-effect, and SSR renders the
  // empty default to avoid a hydration mismatch.
  const selected =
    useSyncExternalStore(subscribeSelectedBu, getSelectedBu, getServerSelectedBu) ??
    "";

  if (!enabled) return null;

  const handleChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setSelectedBu(e.target.value || null);
  };

  return (
    <div className="flex flex-col gap-1">
      <label
        htmlFor="bu-selector"
        className="text-xs font-medium text-gray-400 uppercase tracking-wide"
      >
        BU override (POC)
      </label>
      <select
        id="bu-selector"
        value={selected}
        onChange={handleChange}
        className="rounded-md bg-white/10 px-2 py-1 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
      >
        <option value="">Default (from token)</option>
        {options.map((bu) => (
          <option key={bu} value={bu} className="text-gray-900">
            BU {bu}
          </option>
        ))}
      </select>
    </div>
  );
}
