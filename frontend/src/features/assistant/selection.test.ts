import { describe, expect, it } from "vitest";
import { capture, rebase } from "./selection";
import { initial, reducer } from "../editor/session";
describe("选区与采用保护", () => {
  it("将 emoji 的 UTF16 偏移转换为代码点", () => {
    expect(capture("😀中文", 2, 4)).toEqual({
      body: "😀中文",
      start: 1,
      end: 3,
    });
  });
  it("范围之前的编辑调整位置，范围内编辑取消范围", () => {
    const selection = capture("甲乙丙丁", 1, 3)!;
    expect(rebase(selection, "新增甲乙丙丁")).toEqual({
      body: "新增甲乙丙丁",
      start: 3,
      end: 5,
    });
    expect(rebase(selection, "甲替换丁")).toBeNull();
    expect(rebase(selection, "甲乙丙丁后记")?.start).toBe(1);
  });
  it("迟到的建议不能覆盖手动编辑或切换后的文章", () => {
    const state = initial("first");
    const action = {
      type: "assistant_apply" as const,
      session: "first",
      sequence: 0,
      before: { title: "", body: "" },
      replacement: { title: "建议", body: "建议正文" },
    };
    const edited = reducer(state, { type: "edit", patch: { body: "手写" } });
    expect(reducer(edited, action)).toBe(edited);
    const next = reducer(state, {
      type: "navigate",
      session: "next",
      articleId: null,
    });
    expect(reducer(next, action)).toBe(next);
  });
  it("可撤销最近填入，后续手写后不能撤销覆盖", () => {
    const state = initial("first");
    const filled = reducer(state, {
      type: "assistant_apply",
      session: "first",
      sequence: 0,
      before: { title: "", body: "" },
      replacement: { title: "建议", body: "建议正文" },
    });
    expect(reducer(filled, { type: "assistant_undo" }).draft.body).toBe("");
    const edited = reducer(filled, { type: "edit", patch: { body: "手写" } });
    expect(reducer(edited, { type: "assistant_undo" })).toBe(edited);
    expect(filled.saved.body).toBe("");
  });
});
