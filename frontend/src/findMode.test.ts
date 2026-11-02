import { describe, expect, it } from "vitest";

/** Pure helper mirroring FindScreen retry-mode memory. */
export function nextRetryLive(lastSearchLive: boolean): boolean {
  return lastSearchLive;
}

describe("Find retry mode", () => {
  it("retries the last live search after mode is cleared", () => {
    expect(nextRetryLive(true)).toBe(true);
  });

  it("retries a saved search when the last attempt was saved", () => {
    expect(nextRetryLive(false)).toBe(false);
  });
});
