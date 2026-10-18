import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";
import type { Action, EditorState } from "../editor/session";
import { rebase, type Fragment } from "./selection";

type Content = { title: string; body: string };
interface Turn {
  id: string;
  message: string;
  mode: string;
  status: string;
  error?: string;
  source_changed?: boolean;
  result: {
    reply?: string;
    replacement?: Content;
    diff?: string;
    diff_truncated?: boolean;
  };
  model_provenance?: { requested_model?: string; reported_model?: string };
}
interface Conversation {
  revision: string;
  turns: Turn[];
}
interface Configuration {
  configured: boolean;
  discussion_model: string;
  editing_model: string;
  reuse: { status: string };
}
const names: Record<string, string> = {
  queued: "排队中",
  running: "正在生成",
  review: "待查看",
  accepted: "已取用建议",
  failed: "生成失败",
  interrupted: "生成已中断",
};
const content = (state: EditorState): Content => ({
  title: state.draft.title,
  body: state.draft.body,
});

export function AssistantPanel({
  state,
  getCurrent,
  getSelection,
  dispatch,
}: {
  state: EditorState;
  getCurrent: () => EditorState;
  getSelection: () => Fragment | null;
  dispatch: (action: Action) => void;
}) {
  const client = useQueryClient();
  const [mode, setMode] = useState<"discuss" | "edit">("discuss");
  const [message, setMessage] = useState("");
  const [fragment, setFragment] = useState<Fragment | null>(null);
  const [reference, setReference] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const active = useRef(false);
  const pendingSend = useRef<{ signature: string; id: string } | null>(null);
  const history = useQuery({
    queryKey: ["assistant", state.articleId],
    queryFn: () =>
      request<Conversation>(`/articles/${state.articleId}/assistant`),
    enabled: !!state.articleId && !state.loading,
    refetchInterval: 1500,
  });
  const configuration = useQuery({
    queryKey: ["assistant-configuration"],
    queryFn: () => request<Configuration>("/assistant/configuration"),
  });
  const selection = fragment ? rebase(fragment, state.draft.body) : null;
  const invalidSelection = !!fragment && !selection;
  const running = history.data?.turns.some((t) =>
    ["queued", "running"].includes(t.status),
  );
  const profileChanged = state.draft.profile_id !== state.saved.profile_id;
  async function mutation(operation: () => Promise<unknown>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
      await client.invalidateQueries({
        queryKey: ["assistant", state.articleId],
      });
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  async function send() {
    if (
      !history.data ||
      !state.articleId ||
      !message.trim() ||
      profileChanged ||
      invalidSelection ||
      state.saving
    )
      return;
    const value = getCurrent();
    const text = message;
    const signature = JSON.stringify([
      value.articleId,
      history.data.revision,
      text,
      mode,
      content(value),
      selection,
      reference,
    ]);
    if (pendingSend.current?.signature !== signature)
      pendingSend.current = {
        signature,
        id: crypto.randomUUID().replaceAll("-", ""),
      };
    const requestId = pendingSend.current.id;
    await mutation(async () => {
      await request(`/articles/${value.articleId}/assistant`, {
        request_id: requestId,
        revision: history.data!.revision,
        message: text,
        mode,
        draft: content(value),
        ...(selection
          ? { selection: { start: selection.start, end: selection.end } }
          : {}),
        ...(reference ? { reference_turn_id: reference } : {}),
      });
      setMessage((current) => (current === text ? "" : current));
      setReference(null);
      pendingSend.current = null;
    });
  }
  async function adopt(turn: Turn) {
    const before = getCurrent();
    await mutation(async () => {
      const result = await request<{ replacement: Content }>(
        `/articles/${before.articleId}/assistant/${turn.id}/accept`,
        { draft: content(before) },
      );
      const latest = getCurrent();
      if (
        latest.session !== before.session ||
        latest.sequence !== before.sequence ||
        latest.saving
      )
        throw new Error("等待期间文章已变化，未填入建议。请核对当前稿后再试。");
      dispatch({
        type: "assistant_apply",
        session: before.session,
        sequence: before.sequence,
        before: content(before),
        replacement: result.replacement,
      });
      setFragment(null);
    });
  }
  return (
    <aside className="assistant-panel" aria-label="侧边 AI">
      <header>
        <div>
          <strong>侧边 AI</strong>
          <small>基于当前编辑区讨论或修改</small>
        </div>
        {state.undo && (
          <button
            disabled={busy || !!state.saving}
            onClick={() => dispatch({ type: "assistant_undo" })}
          >
            撤销填入
          </button>
        )}
      </header>
      <div className="assistant-configuration">
        <span>讨论：{configuration.data?.discussion_model || "读取中"}</span>
        <span>修改：{configuration.data?.editing_model || "读取中"}</span>
        <small>
          {configuration.data?.reuse.status === "imported"
            ? "已复用原纸飞机设置"
            : configuration.data?.configured
              ? "沿用已有模型设置"
              : "尚未读取到模型配置，可从原工作台设置检查"}
        </small>
      </div>
      <div className="assistant-history" aria-live="polite">
        {!state.articleId && (
          <p className="assistant-hint">
            先保存文章一次，即可开始对话。之后未保存的文字也会随本次请求提交。
          </p>
        )}
        {state.articleId && !history.data?.turns.length && (
          <p className="assistant-hint">
            可以先问“这段表达有什么问题？”，再切换“修改文章”生成建议稿。
          </p>
        )}
        {history.data?.turns.map((turn) => (
          <article
            className="assistant-turn"
            key={turn.id}
            data-turn-id={turn.id}
          >
            <p className="assistant-message">{turn.message}</p>
            <small>
              {turn.mode === "edit" ? "修改" : "讨论"} ·{" "}
              {names[turn.status] || turn.status}
              {turn.model_provenance?.requested_model
                ? ` · ${turn.model_provenance.reported_model || turn.model_provenance.requested_model}`
                : ""}
            </small>
            {turn.result.reply && <p>{turn.result.reply}</p>}
            {turn.error && <p role="alert">{turn.error}</p>}
            {turn.source_changed && <p>原稿已变化，旧建议仍保留供参考。</p>}
            {turn.result.replacement && (
              <>
                <details>
                  <summary>查看建议与差异</summary>
                  <pre className="assistant-diff">
                    {turn.result.diff}
                    {turn.result.diff_truncated &&
                      "\n差异较长，已截断；下面保留完整建议。"}
                  </pre>
                  <strong>{turn.result.replacement.title}</strong>
                  <pre>{turn.result.replacement.body}</pre>
                </details>
                <div className="assistant-actions">
                  <button
                    disabled={busy || !!state.saving || !!turn.source_changed}
                    onClick={() => void adopt(turn)}
                  >
                    填入编辑区
                  </button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setReference(turn.id);
                      setMode("edit");
                    }}
                  >
                    继续调整
                  </button>
                </div>
              </>
            )}
            {["failed", "interrupted"].includes(turn.status) && (
              <button
                disabled={busy || running}
                onClick={() =>
                  void mutation(() =>
                    request(
                      `/articles/${state.articleId}/assistant/${turn.id}/retry`,
                      {},
                    ),
                  )
                }
              >
                重试本次请求
              </button>
            )}
          </article>
        ))}
      </div>
      <form
        className="assistant-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send();
        }}
      >
        <div className="assistant-actions">
          <button
            type="button"
            aria-pressed={mode === "discuss"}
            onClick={() => setMode("discuss")}
          >
            仅讨论
          </button>
          <button
            type="button"
            aria-pressed={mode === "edit"}
            onClick={() => setMode("edit")}
          >
            修改文章
          </button>
          <button
            type="button"
            onClick={() => {
              const selected = getSelection();
              if (!selected)
                setError("请先在正文中选中文字，再点击使用选中片段。");
              else {
                setFragment(selected);
                setError("");
              }
            }}
          >
            使用选中片段
          </button>
        </div>
        {fragment && (
          <p className={invalidSelection ? "assistant-error" : ""}>
            {invalidSelection
              ? "选中片段已被修改，请重新选择或清除范围。"
              : `仅处理片段：${Array.from(selection!.body).slice(selection!.start, selection!.end).join("").slice(0, 80)}`}{" "}
            <button type="button" onClick={() => setFragment(null)}>
              清除范围
            </button>
          </p>
        )}
        {reference && (
          <p>
            基于上次建议继续调整，使用当前编辑稿。
            <button type="button" onClick={() => setReference(null)}>
              取消承接
            </button>
          </p>
        )}
        {profileChanged && <p>画像已更改，请先保存文章再发送。</p>}
        {(error || history.error || configuration.error) && (
          <p className="assistant-error" role="alert">
            {error || history.error?.message || configuration.error?.message}
          </p>
        )}
        <textarea
          aria-label="给 AI 的要求"
          placeholder={
            mode === "discuss"
              ? "想讨论这篇文章的什么问题？"
              : "希望怎样修改？建议先预览，再决定填入。"
          }
          maxLength={4000}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
        />
        <div className="assistant-actions">
          <small>
            {running ? "请求处理中，可继续编辑文章" : "填入后需自行保存文章"}
          </small>
          <button
            className="primary"
            disabled={
              !state.articleId ||
              !history.data ||
              busy ||
              running ||
              profileChanged ||
              invalidSelection ||
              !!state.saving ||
              !message.trim()
            }
            type="submit"
          >
            {busy ? "提交中…" : "发送"}
          </button>
        </div>
      </form>
    </aside>
  );
}
