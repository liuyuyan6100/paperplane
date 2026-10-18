import type { Article, Draft } from "./features/editor/session";
export interface Session {
  token: string;
  version: string;
  milestone: string;
  data_dir: string;
  legacy_url: string;
}
export interface ArticleRow {
  id: string;
  title: string;
  summary: string;
  stage: Draft["stage"];
  version: number;
  updated_at: string;
}
let token = "";
export async function request<T>(
  url: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  const res = await fetch("/api/v2" + url, {
    cache: "no-store",
    signal,
    ...(body === undefined
      ? {}
      : {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Studio-Token": token,
          },
          body: JSON.stringify(body),
        }),
  });
  let value;
  try {
    value = await res.json();
  } catch {
    throw new Error("服务响应不完整，请保留当前文字后重试。");
  }
  if (!res.ok)
    throw Object.assign(
      new Error(value.error || "请求失败，请保留当前文字后重试。"),
      { status: res.status, details: value.details },
    );
  return value as T;
}
export async function bootstrap() {
  const session = await request<Session>("/session");
  token = session.token;
  return session;
}
export const loadArticle = (id: string) => request<Article>("/articles/" + id);
export const saveArticle = (
  draft: Draft,
  id: string | null,
  version: number | null,
) =>
  request<Article>(id ? "/articles/" + id : "/articles", {
    ...draft,
    ...(id ? { version } : {}),
  });
