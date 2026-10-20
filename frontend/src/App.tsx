import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { CreationPanel } from "./features/creation/CreationPanel";
import { TasksButton } from "./features/tasks/TaskCenter";
import { MetadataAssistant } from "./features/editor/MetadataAssistant";
import { AssistantPanel } from "./features/assistant/AssistantPanel";
import {
  EditorSplit,
  readLayoutPreference,
  saveLayoutPreference,
} from "./features/editor/EditorSplit";
import { capture } from "./features/assistant/selection";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  bootstrap,
  loadArticle,
  request,
  saveArticle,
  type ArticleRow,
} from "./api";
import {
  initial,
  isDirty,
  reducer,
  type Draft,
  type SaveRequest,
} from "./features/editor/session";

declare global {
  interface Window {
    PaperPlaneDesktop?: {
      hasUnsaved: () => boolean;
      getUnsavedAreas: () => string[];
    };
  }
}
const stageNames = {
  idea: "选题中",
  draft: "写作中",
  ready: "待发布",
  published: "已发布",
};
const sample =
  "# 编辑验收样稿\n\n这里是一篇供你熟悉操作的测试稿。\n\n## 试着修改\n\n- 添加一段文字\n- 预览列表和**加粗**\n- 保存后重新打开\n\n这份样稿只保存在架构验证版。";

export function App() {
  const session = useQuery({
    queryKey: ["session"],
    queryFn: bootstrap,
    staleTime: Infinity,
  });
  const articles = useQuery({
    queryKey: ["articles"],
    queryFn: () => request<{ articles: ArticleRow[] }>("/articles"),
    enabled: !!session.data,
    refetchInterval: 5000,
  });
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: () =>
      request<{ profiles: { id: string; name: string }[] }>("/profiles"),
    enabled: !!session.data,
  });
  const client = useQueryClient();
  const [state, dispatch] = useReducer(reducer, undefined, () =>
    initial(crypto.randomUUID()),
  );
  const current = useRef(state);
  const bodyInput = useRef<HTMLTextAreaElement>(null);
  current.current = state;
  const saveBusy = useRef(false);
  const creationDirty = useRef(false);
  const creationUnsavedAreas = useRef<string[]>([]);
  const setCreationDirty = useCallback((value: boolean, areas: string[] = []) => {
    creationDirty.current = value;
    creationUnsavedAreas.current = value ? areas : [];
  }, []);
  const [search, setSearch] = useState("");
  const [preview, setPreview] = useState(false);
  const [previewHtml, setPreviewHtml] = useState("");
  const [previewError, setPreviewError] = useState("");
  const [fields, setFields] = useState(false);
  const [mobileAI, setMobileAI] = useState(false);
  const [mobileLibrary, setMobileLibrary] = useState(false);
  const [aiCollapsed, setAiCollapsed] = useState(
    () => !!readLayoutPreference("paperplane.ai.collapsed", 0),
  );
  const dirty = isDirty(state);

  useEffect(() => {
    const dirtyNow = () =>
      isDirty(current.current) ||
      !!current.current.saving ||
      creationDirty.current;
    const getUnsavedAreas = () => [
      ...(isDirty(current.current) ? ["文章编辑区"] : []),
      ...(current.current.saving ? ["文章正在保存，请等待完成"] : []),
      ...(creationDirty.current
        ? creationUnsavedAreas.current.length
          ? creationUnsavedAreas.current
          : ["素材与创作"]
        : []),
    ];
    window.PaperPlaneDesktop = { hasUnsaved: dirtyNow, getUnsavedAreas };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyNow()) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      delete window.PaperPlaneDesktop;
    };
  }, []);

  useEffect(() => {
    if (!preview || !session.data) return;
    const controller = new AbortController();
    setPreviewHtml("");
    setPreviewError("");
    const timer = setTimeout(() => {
      request<{ html: string }>(
        "/preview",
        { body: state.draft.body },
        controller.signal,
      )
        .then((value) => {
          if (!controller.signal.aborted) setPreviewHtml(value.html);
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setPreviewError(String(error.message));
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [preview, state.draft.body, session.data]);

  async function navigate(id: string | null, seeded = false) {
    if (
      (isDirty(current.current) ||
        current.current.saving ||
        creationDirty.current) &&
      !confirm(
        "当前文章有未保存修改或正在保存。离开会放弃本页未保存内容，是否继续？",
      )
    )
      return;
    const key = crypto.randomUUID();
    setMobileLibrary(false);
    dispatch({ type: "navigate", session: key, articleId: id });
    if (id) {
      try {
        dispatch({
          type: "loaded",
          session: key,
          article: await loadArticle(id),
        });
      } catch (error) {
        dispatch({
          type: "failed",
          session: key,
          error: (error as Error).message,
        });
      }
    } else if (seeded)
      dispatch({
        type: "edit",
        patch: { title: "编辑验收样稿", body: sample },
      });
  }

  async function save() {
    const value = current.current;
    if (
      saveBusy.current ||
      value.loading ||
      value.saving ||
      !value.draft.title.trim()
    )
      return;
    saveBusy.current = true;
    const saveRequest: SaveRequest = {
      id: crypto.randomUUID(),
      session: value.session,
      sequence: value.sequence,
      draft: structuredClone(value.draft),
    };
    dispatch({ type: "saving", request: saveRequest });
    try {
      const article = await saveArticle(
        saveRequest.draft,
        value.articleId,
        value.version,
      );
      dispatch({ type: "saved", request: saveRequest, article });
      void client.invalidateQueries({ queryKey: ["articles"] });
    } catch (error) {
      dispatch({
        type: "failed",
        session: value.session,
        requestId: saveRequest.id,
        error: (error as Error).message,
      });
    } finally {
      saveBusy.current = false;
    }
  }

  function compatibility() {
    if (
      (dirty || state.saving || creationDirty.current) &&
      !confirm(
        "请先保存需要保留的修改。现在离开新编辑器并打开同一验证库的原工作台？",
      )
    )
      return;
    location.href = "/";
  }
  function download() {
    const file = new Blob([JSON.stringify(state.draft, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(file);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "paperplane-editor-draft.json";
    anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 500);
  }
  const list =
    articles.data?.articles.filter((a) =>
      (a.title + a.summary).toLowerCase().includes(search.toLowerCase()),
    ) ?? [];
  const newer = articles.data?.articles.find(
    (a) => a.id === state.articleId && a.version > (state.version ?? 0),
  );
  const edit = (patch: Partial<Draft>) => dispatch({ type: "edit", patch });

  if (!session.data)
    return (
      <main className="startup">
        <h1>纸飞机 · 文章编辑验证版</h1>
        <p role="status">{session.error?.message || "正在连接本机验证服务…"}</p>
        <button onClick={() => void session.refetch()}>重新连接</button>
      </main>
    );

  return (
    <div className={"workspace" + (mobileLibrary ? " library-open" : "")}>
      <aside className="library">
        <a className="brand" href="/editor/">
          <span className="brand-mark">↗</span>
          <span>
            纸飞机<small>架构验证版 · M4</small>
          </span>
        </a>
        <button className="primary new" onClick={() => void navigate(null)}>
          ＋ 新建文章
        </button>
        <label className="search">
          <span>搜索文章</span>
          <input
            placeholder="标题或摘要"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <div className="list-heading">
          验证库文章 <span>{articles.data?.articles.length ?? 0}</span>
        </div>
        <div className="article-list">
          {list.map((a) => (
            <button
              key={a.id}
              className={
                "article-item" + (state.articleId === a.id ? " selected" : "")
              }
              onClick={() => void navigate(a.id)}
            >
              <strong>{a.title}</strong>
              <span>{a.summary || "暂无摘要"}</span>
              <small>
                {stageNames[a.stage]} · 第 {a.version} 版
              </small>
            </button>
          ))}
          {!list.length && (
            <div className="empty-list">
              <p>
                {articles.error?.message || "还没有文章，从一篇测试稿开始。"}
              </p>
              <button onClick={() => void navigate(null, true)}>
                创建验收样稿
              </button>
            </div>
          )}
        </div>
        <button className="legacy" onClick={compatibility}>
          打开原工作台 ↗
        </button>
        <p className="library-note">原功能连接当前验证库</p>
      </aside>
      <main className="editor-main">
        <header className="toolbar">
          <div>
            <span className="milestone">M4 · 素材、提纲与正文采用</span>
            <h1>{state.articleId ? "编辑文章" : "开始一篇新文章"}</h1>
          </div>
          <div className="toolbar-actions">
            <button
              className="mobile-control"
              onClick={() => setMobileLibrary(!mobileLibrary)}
            >
              文章列表
            </button>
            <button
              className="mobile-control"
              onClick={() => setMobileAI(!mobileAI)}
            >
              {mobileAI ? "返回文章" : "显示 AI"}
            </button>
            <CreationPanel
              key={state.session}
              state={state}
              getCurrent={() => current.current}
              onDirty={setCreationDirty}
              onAdopt={async (id, values) => {
                const value = current.current;
                if (
                  saveBusy.current ||
                  value.saving ||
                  value.loading ||
                  isDirty(value)
                )
                  throw new Error("请先保存当前文章，再核对采用。");
                saveBusy.current = true;
                const adoption: SaveRequest = {
                  id: crypto.randomUUID(),
                  session: value.session,
                  sequence: value.sequence,
                  draft: structuredClone(value.draft),
                };
                dispatch({ type: "saving", request: adoption });
                try {
                  const result = await request<{
                    post: import("./features/editor/session").Article;
                  }>(
                    `/articles/${value.articleId}/creation/${id}/adopt`,
                    values,
                  );
                  dispatch({
                    type: "saved",
                    request: adoption,
                    article: result.post,
                  });
                  void client.invalidateQueries({ queryKey: ["articles"] });
                } catch (err) {
                  dispatch({
                    type: "failed",
                    session: value.session,
                    requestId: adoption.id,
                    error: (err as Error).message,
                  });
                  throw err;
                } finally {
                  saveBusy.current = false;
                }
              }}
            />
            <TasksButton
              articleId={state.articleId}
              onOpenArticle={(id) => {
                setFields(true);
                if (id !== current.current.articleId) void navigate(id);
              }}
            />
            <button
              className="desktop-ai-toggle"
              aria-expanded={!aiCollapsed}
              onClick={() => {
                setAiCollapsed(!aiCollapsed);
                saveLayoutPreference(
                  "paperplane.ai.collapsed",
                  aiCollapsed ? 0 : 1,
                );
              }}
            >
              {aiCollapsed ? "展开 AI" : "收起 AI"}
            </button>
            <span role="status" className={dirty ? "dirty" : "saved"}>
              {state.saving
                ? "正在保存…"
                : dirty
                  ? "有未保存修改"
                  : state.version
                    ? `已保存 · v${state.version}`
                    : "新草稿"}
            </span>
            <button aria-pressed={preview} onClick={() => setPreview(!preview)}>
              {preview ? "收起预览" : "预览"}
            </button>
            <button
              className="primary"
              disabled={
                !!state.saving || state.loading || !state.draft.title.trim()
              }
              onClick={() => void save()}
            >
              保存文章
            </button>
          </div>
        </header>
        <div className="editor-notices">
          {state.error && (
            <div className="error" role="alert">
              {state.error} 当前文字仍保留，可先下载当前稿。
              <button onClick={download}>下载当前稿</button>
            </div>
          )}
          {newer && (
            <div className="warning">
              另一页面已保存 v{newer.version}，当前编辑内容保持不变。
              <button onClick={() => void navigate(state.articleId)}>
                重新读取
              </button>
            </div>
          )}
          {state.notice && (
            <p className="notice" role="status">
              {state.notice}
            </p>
          )}
        </div>
        {state.loading ? (
          <p className="loading" role="status">
            正在读取文章…
          </p>
        ) : (
          <EditorSplit
            collapsed={aiCollapsed}
            mobileAI={mobileAI}
            assistant={
              <AssistantPanel
                key={state.session}
                state={state}
                getCurrent={() => current.current}
                getSelection={() =>
                  bodyInput.current
                    ? capture(
                        current.current.draft.body,
                        bodyInput.current.selectionStart,
                        bodyInput.current.selectionEnd,
                      )
                    : null
                }
                dispatch={dispatch}
              />
            }
          >
            <div className="editing-document">
              <section className="document-meta">
                <label className="title-label">
                  <span>文章标题</span>
                  <input
                    className="title-input"
                    aria-label="文章标题"
                    value={state.draft.title}
                    maxLength={150}
                    placeholder="写下这篇文章的标题"
                    onChange={(e) => edit({ title: e.target.value })}
                  />
                </label>
                <div className="meta-line">
                  <label>
                    内容阶段
                    <select
                      value={state.draft.stage}
                      onChange={(e) =>
                        edit({ stage: e.target.value as Draft["stage"] })
                      }
                    >
                      {Object.entries(stageNames).map(([k, v]) => (
                        <option key={k} value={k}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    账号画像
                    <select
                      value={state.draft.profile_id ?? ""}
                      onChange={(e) =>
                        edit({ profile_id: e.target.value || null })
                      }
                    >
                      <option value="">不关联画像</option>
                      {profiles.data?.profiles.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <button
                    aria-expanded={fields}
                    onClick={() => setFields(!fields)}
                  >
                    {fields ? "收起文章信息" : "摘要、署名与更多信息"}
                  </button>
                </div>
                {fields && (
                  <div className="extra-fields">
                    <MetadataAssistant
                      key={state.session}
                      state={state}
                      getCurrent={() => current.current}
                      dispatch={dispatch}
                    />
                    <label>
                      摘要
                      <textarea
                        aria-label="摘要"
                        value={state.draft.summary}
                        maxLength={300}
                        onChange={(e) => edit({ summary: e.target.value })}
                      />
                    </label>
                    <label>
                      作者
                      <input
                        value={state.draft.author}
                        maxLength={60}
                        onChange={(e) => edit({ author: e.target.value })}
                      />
                    </label>
                    <label>
                      标签（逗号分隔）
                      <input
                        value={state.draft.tags.join(",")}
                        onChange={(e) =>
                          edit({ tags: e.target.value.split(/[,，]/) })
                        }
                        onBlur={() =>
                          edit({
                            tags: state.draft.tags
                              .map((t) => t.trim())
                              .filter(Boolean),
                          })
                        }
                      />
                    </label>
                    <label>
                      封面地址
                      <input
                        value={state.draft.cover}
                        maxLength={2000}
                        onChange={(e) => edit({ cover: e.target.value })}
                      />
                    </label>
                    <label>
                      来源
                      <input
                        value={state.draft.source}
                        maxLength={2000}
                        onChange={(e) => edit({ source: e.target.value })}
                      />
                    </label>
                  </div>
                )}
              </section>
              <section
                className={"writing-space" + (preview ? " with-preview" : "")}
              >
                <div className="writing-pane">
                  <div className="pane-heading">
                    <span>正文 · Markdown</span>
                    <small>
                      {state.draft.body.length.toLocaleString()} 字符
                    </small>
                  </div>
                  <textarea
                    ref={bodyInput}
                    className="body-input"
                    aria-label="文章正文"
                    spellCheck={false}
                    value={state.draft.body}
                    placeholder="从这里开始写作，支持 Markdown…"
                    onChange={(e) => edit({ body: e.target.value })}
                  />
                </div>
                {preview && (
                  <div className="preview-pane">
                    <div className="pane-heading">阅读预览</div>
                    {previewError ? (
                      <p role="alert">{previewError}</p>
                    ) : (
                      <article
                        className="prose"
                        dangerouslySetInnerHTML={{ __html: previewHtml }}
                      />
                    )}
                  </div>
                )}
              </section>
            </div>
          </EditorSplit>
        )}
        <footer>
          <details>
            <summary>验证版信息与本轮范围</summary>
            <p>
              版本 {session.data.version}。当前可验证文章编辑、侧边 AI
              与任务状态。 其他功能可通过原工作台使用。
            </p>
            <p className="data-path">数据目录：{session.data.data_dir}</p>
            <p>
              <a href="/editor/" target="_blank" rel="noopener noreferrer">
                在浏览器另开验证版，核对多窗口编辑
              </a>
            </p>
            <button onClick={download}>下载当前稿（保留文字）</button>
          </details>
        </footer>
      </main>
    </div>
  );
}
