import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";
import type { Action, EditorState } from "../editor/session";
import { rebase, type Fragment } from "./selection";
import {
  ModelSettings,
  type Catalog,
  type ModelSettingsData,
} from "./ModelSettings";

import { SuggestionReview, type Content } from "./SuggestionReview";
interface Turn {
  id: string;
  message: string;
  mode: string;
  status: string;
  error?: string;
  source_changed?: boolean;
  draft?: Content;
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
  image_model: string;
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
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [reviewedId, setReviewedId] = useState<string | null>(null);
  const [chosenModels, setChosenModels] = useState({ discuss: "", edit: "" });
  const scroller = useRef<HTMLDivElement>(null);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const followConversation = useRef(true);
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
    refetchInterval: 5000,
  });
  const catalog = useQuery({
    queryKey: ["model-catalog", "text"],
    queryFn: () => request<Catalog>("/assistant/models"),
    retry: false,
    staleTime: 60000,
  });
  const settings = useQuery({
    queryKey: ["model-settings"],
    queryFn: () => request<ModelSettingsData>("/assistant/settings"),
    staleTime: 5000,
  });
  const model = chosenModels[mode];
  const defaultModel =
    mode === "discuss"
      ? configuration.data?.discussion_model
      : configuration.data?.editing_model;
  const knownModels = [
    ...new Set(
      [
        ...(catalog.data?.models ?? []),
        configuration.data?.discussion_model,
        configuration.data?.editing_model,
        model,
      ].filter((id): id is string => !!id),
    ),
  ];
  const visual = settings.data?.profiles.find(
    (profile) => profile.id === state.draft.profile_id,
  );
  const selection = fragment ? rebase(fragment, state.draft.body) : null;
  const invalidSelection = !!fragment && !selection;
  const running = history.data?.turns.some((t) =>
    ["queued", "running"].includes(t.status),
  );
  const profileChanged = state.draft.profile_id !== state.saved.profile_id;
  const canSend =
    !!state.articleId &&
    !!history.data &&
    !busy &&
    !running &&
    !profileChanged &&
    !invalidSelection &&
    !state.saving &&
    !!message.trim();
  useEffect(() => {
    if (followConversation.current && scroller.current)
      scroller.current.scrollTop = scroller.current.scrollHeight;
  }, [history.data]);
  async function mutation(operation: () => Promise<unknown>) {
    if (active.current) return false;
    active.current = true;
    setBusy(true);
    setError("");
    try {
      await operation();
      await client.invalidateQueries({
        queryKey: ["assistant", state.articleId],
      });
      return true;
    } catch (failure) {
      setError((failure as Error).message);
      return false;
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  async function send() {
    if (
      !canSend ||
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
      model,
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
        ...(model ? { model } : {}),
        draft: content(value),
        ...(selection
          ? { selection: { start: selection.start, end: selection.end } }
          : {}),
        ...(reference ? { reference_turn_id: reference } : {}),
      });
      setMessage((current) => (current === text ? "" : current));
      setReference(null);
      pendingSend.current = null;
      followConversation.current = true;
    });
  }
  async function adopt(turn: Turn) {
    const before = getCurrent();
    return mutation(async () => {
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
  const isStale = (turn: Turn) =>
    !!turn.source_changed ||
    !turn.draft ||
    turn.draft.title !== state.draft.title ||
    turn.draft.body !== state.draft.body;
  function continueFrom(turn: Turn) {
    setReference(turn.id);
    setMode("edit");
    setFragment(null);
    setError("");
    composerInput.current?.focus();
    composerInput.current?.scrollIntoView({ block: "nearest" });
  }
  const reviewed = history.data?.turns.find((turn) => turn.id === reviewedId);
  const reviewStale = !!reviewed && isStale(reviewed);
  return (
    <aside className="assistant-panel" aria-label="侧边 AI">
      <header>
        <div>
          <strong>侧边 AI</strong>
          <small>基于当前编辑区讨论或修改</small>
        </div>
        <button onClick={() => setSettingsOpen(true)}>模型与生图设置</button>
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
        <label>
          本次{mode === "discuss" ? "讨论" : "修改"}模型
          <select
            aria-label="本次使用模型"
            value={model}
            onChange={(event) =>
              setChosenModels({ ...chosenModels, [mode]: event.target.value })
            }
          >
            <option value="">跟随默认：{defaultModel || "读取中"}</option>
            {knownModels.map((id) => (
              <option value={id} key={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
        <div className="assistant-model-tools">
          <small>
            {catalog.isFetching
              ? "正在识别模型目录…"
              : catalog.error
                ? "目录读取失败，仍可用已有配置"
                : `已识别 ${catalog.data?.models.length ?? 0} 个模型`}
          </small>
          <button
            onClick={() => void catalog.refetch()}
            disabled={catalog.isFetching}
          >
            刷新目录
          </button>
        </div>
        <small>
          生图：{configuration.data?.image_model || "未配置"} · 风格：
          {visual?.visual_style ? visual.name : "选择文章画像后应用"}
        </small>
        <small>
          {configuration.data?.reuse.status === "imported"
            ? "已复用原纸飞机设置"
            : configuration.data?.configured
              ? "沿用已有模型设置"
              : "配置尚未齐全，点击“模型与生图设置”补齐"}
        </small>
      </div>
      <div
        ref={scroller}
        className="assistant-history"
        aria-live="polite"
        onScroll={(event) => {
          const node = event.currentTarget;
          followConversation.current =
            node.scrollHeight - node.scrollTop - node.clientHeight < 60;
        }}
      >
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
            {turn.result.replacement && isStale(turn) && (
              <p className="assistant-history-note">
                {turn.status === "accepted"
                  ? "这条建议已有取用记录。继续修改请基于当前稿生成新建议。"
                  : "文章或写作依据已变化，这条旧建议仅供参考。可基于当前稿生成新建议。"}
              </p>
            )}
            {turn.result.replacement && (
              <>
                <button
                  className="review-open"
                  onClick={() => {
                    setError("");
                    setReviewedId(turn.id);
                  }}
                >
                  查看修改对比 <span aria-hidden="true">↗</span>
                </button>
                <div className="assistant-actions">
                  {!isStale(turn) && (
                    <button
                      disabled={busy || !!state.saving}
                      onClick={() => void adopt(turn)}
                    >
                      填入编辑区
                    </button>
                  )}
                  <button disabled={busy} onClick={() => continueFrom(turn)}>
                    基于当前稿继续修改
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
            参考这条历史建议，基于{fragment ? "当前选区" : "当前编辑稿全文"}继续。请填写新的修改要求。
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
          ref={composerInput}
          aria-label="给 AI 的要求"
          placeholder={
            mode === "discuss"
              ? "想讨论这篇文章的什么问题？"
              : "希望怎样修改？建议先预览，再决定填入。"
          }
          maxLength={4000}
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.ctrlKey || event.metaKey) &&
              !event.nativeEvent.isComposing &&
              !event.repeat
            ) {
              event.preventDefault();
              if (canSend) void send();
            }
          }}
        />
        <div className="assistant-actions">
          <small>
            {running
              ? "请求处理中，可继续编辑文章"
              : "Ctrl+Enter 发送 · 填入后自行保存"}
          </small>
          <button className="primary" disabled={!canSend} type="submit">
            {busy ? "提交中…" : "发送"}
          </button>
        </div>
      </form>
      {reviewed?.result.replacement && (
        <SuggestionReview
          before={reviewed.draft}
          after={reviewed.result.replacement}
          stale={reviewStale}
          busy={busy || !!state.saving}
          error={error}
          onClose={() => setReviewedId(null)}
          onContinue={() => continueFrom(reviewed)}
          onAdopt={() =>
            void adopt(reviewed).then((applied) => {
              if (applied) setReviewedId(null);
            })
          }
        />
      )}
      {settingsOpen && (
        <ModelSettings
          onClose={() => {
            setSettingsOpen(false);
            void client.invalidateQueries({ queryKey: ["profiles"] });
            void client.invalidateQueries({ queryKey: ["model-settings"] });
            void client.invalidateQueries({
              queryKey: ["assistant-configuration"],
            });
          }}
        />
      )}
    </aside>
  );
}
