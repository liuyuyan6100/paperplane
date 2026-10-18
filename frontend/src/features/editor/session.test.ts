import { describe, expect, it } from "vitest";
import {
  blank,
  initial,
  isDirty,
  reducer,
  type Article,
  type SaveRequest,
} from "./session";
const article = (body: string, version = 1): Article => ({
  ...blank(),
  id: "a".repeat(32),
  title: "样稿",
  body,
  version,
  updated_at: "",
});
const opened = () =>
  reducer(initial("first", "a".repeat(32)), {
    type: "loaded",
    session: "first",
    article: article("原稿"),
  });
describe("编辑会话竞争保护", () => {
  it("保存期间继续输入，响应只确认已提交内容", () => {
    let state = reducer(opened(), {
      type: "edit",
      patch: { body: "第一次修改" },
    });
    const request: SaveRequest = {
      id: "save-1",
      session: state.session,
      sequence: state.sequence,
      draft: state.draft,
    };
    state = reducer(state, { type: "saving", request });
    state = reducer(state, { type: "edit", patch: { body: "随后修改😀" } });
    state = reducer(state, {
      type: "saved",
      request,
      article: article("第一次修改", 2),
    });
    expect(state.draft.body).toBe("随后修改😀");
    expect(state.saved.body).toBe("第一次修改");
    expect(state.version).toBe(2);
    expect(isDirty(state)).toBe(true);
  });
  it("切换文章后忽略旧文章读回及保存响应", () => {
    const old = opened();
    const request: SaveRequest = {
      id: "save",
      session: old.session,
      sequence: 0,
      draft: old.draft,
    };
    const next = reducer(old, {
      type: "navigate",
      session: "next",
      articleId: "b".repeat(32),
    });
    expect(
      reducer(next, {
        type: "loaded",
        session: "first",
        article: article("迟到原稿"),
      }),
    ).toBe(next);
    expect(
      reducer(next, {
        type: "saved",
        request,
        article: article("迟到保存", 2),
      }),
    ).toBe(next);
  });
  it("冲突或网络失败保留本地文字和保存基准", () => {
    let state = reducer(opened(), {
      type: "edit",
      patch: { body: "不能丢失的修改" },
    });
    const request = {
      id: "save",
      session: state.session,
      sequence: state.sequence,
      draft: state.draft,
    };
    state = reducer(state, { type: "saving", request });
    state = reducer(state, {
      type: "failed",
      session: state.session,
      requestId: "save",
      error: "版本冲突",
    });
    expect(state.draft.body).toBe("不能丢失的修改");
    expect(state.version).toBe(1);
    expect(isDirty(state)).toBe(true);
  });
  it("重复旧保存回执不能降低版本或重置编辑", () => {
    let state = opened();
    const request = {
      id: "old",
      session: state.session,
      sequence: 0,
      draft: state.draft,
    };
    state = reducer(state, { type: "saving", request });
    state = reducer(state, {
      type: "saved",
      request,
      article: article("原稿", 2),
    });
    expect(
      reducer(state, { type: "saved", request, article: article("错误", 1) }),
    ).toBe(state);
  });
  it("新文章保存也保留请求发出后的输入", () => {
    let state = reducer(initial("new"), {
      type: "edit",
      patch: { title: "新稿", body: "第一段" },
    });
    const request = {
      id: "create",
      session: state.session,
      sequence: state.sequence,
      draft: state.draft,
    };
    state = reducer(state, { type: "saving", request });
    state = reducer(state, { type: "edit", patch: { body: "第二段" } });
    state = reducer(state, {
      type: "saved",
      request,
      article: { ...article("第一段"), title: "新稿" },
    });
    expect(state.articleId).toBe("a".repeat(32));
    expect(state.draft.body).toBe("第二段");
    expect(isDirty(state)).toBe(true);
  });
});

describe("文章信息补齐的编辑竞争保护", () => {
  it("只补齐空白信息，保留正文、来源和手动署名", () => {
    const state = reducer(opened(), {
      type: "edit",
      patch: { author: "手写署名", source: "https://example.com/" },
    });
    const next = reducer(state, {
      type: "metadata_apply",
      session: state.session,
      sequence: state.sequence,
      before: state.draft,
      replacement: { summary: "建议摘要", author: "画像署名", tags: ["标签"] },
    });
    expect(next.draft.author).toBe("手写署名");
    expect(next.draft.body).toBe("原稿");
    expect(next.draft.source).toBe("https://example.com/");
    expect(next.draft.summary).toBe("建议摘要");
    expect(isDirty(next)).toBe(true);
  });
  it("晚到结果不能覆盖后续输入、保存或其他文章", () => {
    const state = opened();
    const action = {
      type: "metadata_apply" as const,
      session: state.session,
      sequence: state.sequence,
      before: state.draft,
      replacement: { summary: "旧建议", author: "署名", tags: [] },
    };
    const edited = reducer(state, {
      type: "edit",
      patch: { summary: "后续手写" },
    });
    expect(reducer(edited, action)).toBe(edited);
    const other = initial("另一文章");
    expect(reducer(other, action)).toBe(other);
    const saving = reducer(state, {
      type: "saving",
      request: {
        id: "saving",
        session: state.session,
        sequence: state.sequence,
        draft: state.draft,
      },
    });
    expect(reducer(saving, action)).toBe(saving);
  });
});
