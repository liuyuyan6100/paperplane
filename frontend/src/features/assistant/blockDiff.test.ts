import { describe, expect, it } from "vitest";
import { changedBlocks } from "./blockDiff";

describe("rendered article block alignment", () => {
  it("keeps repeated paragraphs in sequence and separates inserts from removals", () => {
    const result = changedBlocks(
      ["heading", "same", "old", "same", "end"],
      ["heading", "same", "same", "new", "end"],
    );
    expect([...result.left]).toEqual([2]);
    expect([...result.right]).toEqual([3]);
    expect(result.approximate).toBe(false);
  });
  it("marks formatting changes, empty articles and complete removal without losing indices", () => {
    expect([
      ...changedBlocks(["<p>a</p>"], ["<p><strong>a</strong></p>"]).right,
    ]).toEqual([0]);
    expect([...changedBlocks([], ["new"]).right]).toEqual([0]);
    expect([...changedBlocks(["old"], []).left]).toEqual([0]);
    expect(changedBlocks(["same"], ["same"]).left.size).toBe(0);
  });
  it("bounds work for long articles and retains common ends", () => {
    const original = Array.from({ length: 1000 }, (_, i) => String(i));
    const revised = [...original];
    revised[500] = "changed";
    const result = changedBlocks(original, revised);
    expect(result.approximate).toBe(true);
    expect([...result.left]).toEqual([500]);
    expect([...result.right]).toEqual([500]);
  });
});
