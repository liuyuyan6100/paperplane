export interface Draft {
  title: string;
  body: string;
  summary: string;
  author: string;
  tags: string[];
  cover: string;
  source: string;
  stage: "idea" | "draft" | "ready" | "published";
  profile_id: string | null;
}
export interface Article extends Draft {
  id: string;
  version: number;
  updated_at: string;
}
export interface SaveRequest {
  id: string;
  session: string;
  sequence: number;
  draft: Draft;
}
export interface EditorState {
  undo: {
    before: Pick<Draft, "title" | "body">;
    after: Pick<Draft, "title" | "body">;
    sequence: number;
  } | null;
  session: string;
  articleId: string | null;
  version: number | null;
  draft: Draft;
  saved: Draft;
  sequence: number;
  loading: boolean;
  saving: SaveRequest | null;
  error: string;
  notice: string;
}
export const blank = (): Draft => ({
  title: "",
  body: "",
  summary: "",
  author: "",
  tags: [],
  cover: "",
  source: "",
  stage: "draft",
  profile_id: null,
});
export const toDraft = (a: Draft): Draft => ({
  title: a.title,
  body: a.body,
  summary: a.summary,
  author: a.author,
  tags: [...a.tags],
  cover: a.cover,
  source: a.source,
  stage: a.stage,
  profile_id: a.profile_id ?? null,
});
export const initial = (
  session = "",
  articleId: string | null = null,
): EditorState => ({
  undo: null,
  session,
  articleId,
  version: null,
  draft: blank(),
  saved: blank(),
  sequence: 0,
  loading: articleId !== null,
  saving: null,
  error: "",
  notice: "",
});
export const isDirty = (s: EditorState) =>
  JSON.stringify(s.draft) !== JSON.stringify(s.saved);
export type Action =
  | {
      type: "assistant_apply";
      session: string;
      sequence: number;
      before: Pick<Draft, "title" | "body">;
      replacement: Pick<Draft, "title" | "body">;
    }
  | { type: "assistant_undo" }
  | { type: "navigate"; session: string; articleId: string | null }
  | { type: "loaded"; session: string; article: Article }
  | { type: "edit"; patch: Partial<Draft> }
  | { type: "saving"; request: SaveRequest }
  | { type: "saved"; request: SaveRequest; article: Article }
  | { type: "failed"; session: string; error: string; requestId?: string };

export function reducer(state: EditorState, action: Action): EditorState {
  switch (action.type) {
    case "assistant_apply":
      if (
        state.session !== action.session ||
        state.sequence !== action.sequence ||
        state.saving ||
        state.loading ||
        state.draft.title !== action.before.title ||
        state.draft.body !== action.before.body
      )
        return state;
      return {
        ...state,
        draft: { ...state.draft, ...action.replacement },
        sequence: state.sequence + 1,
        undo: {
          before: action.before,
          after: action.replacement,
          sequence: state.sequence + 1,
        },
        notice: "建议已填入编辑区，尚未保存。",
        error: "",
      };
    case "assistant_undo":
      if (
        !state.undo ||
        state.sequence !== state.undo.sequence ||
        state.draft.title !== state.undo.after.title ||
        state.draft.body !== state.undo.after.body ||
        state.saving
      )
        return state;
      return {
        ...state,
        draft: { ...state.draft, ...state.undo.before },
        sequence: state.sequence + 1,
        undo: null,
        notice: "已撤销本次 AI 填入，尚未保存。",
      };
    case "navigate":
      return initial(action.session, action.articleId);
    case "loaded":
      if (state.session !== action.session || !state.loading) return state;
      return {
        ...state,
        loading: false,
        articleId: action.article.id,
        version: action.article.version,
        draft: toDraft(action.article),
        saved: toDraft(action.article),
      };
    case "edit":
      return {
        ...state,
        undo: null,
        draft: { ...state.draft, ...action.patch },
        sequence: state.sequence + 1,
        error: "",
        notice: "",
      };
    case "saving":
      return state.session !== action.request.session
        ? state
        : { ...state, saving: action.request, error: "", notice: "" };
    case "saved": {
      if (
        state.session !== action.request.session ||
        state.saving?.id !== action.request.id
      )
        return state;
      const changed = state.sequence !== action.request.sequence;
      return {
        ...state,
        articleId: action.article.id,
        version: action.article.version,
        saved: toDraft(action.article),
        draft: changed ? state.draft : toDraft(action.article),
        saving: null,
        notice: changed
          ? "本次提交已保存；随后输入的内容仍未保存。"
          : `已保存为第 ${action.article.version} 版`,
      };
    }
    case "failed":
      if (
        state.session !== action.session ||
        (action.requestId && state.saving?.id !== action.requestId)
      )
        return state;
      return { ...state, loading: false, saving: null, error: action.error };
  }
}
