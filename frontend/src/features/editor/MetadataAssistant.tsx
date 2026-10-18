import { useRef, useState, type Dispatch } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";
import { toDraft, type Action, type Draft, type EditorState } from "./session";

interface Turn {
  id: string;
  status: string;
  source_changed: boolean;
  draft: Draft;
  error: string;
  result: {
    replacement?: Pick<Draft, "summary" | "author" | "tags">;
    author_source?: string;
  };
}
interface History {
  revision: string;
  turns: Turn[];
  planned_model: { model?: string };
}
const same = (a: Draft, b: Draft) =>
  JSON.stringify(toDraft(a)) === JSON.stringify(toDraft(b));

export function MetadataAssistant({
  state,
  getCurrent,
  dispatch,
}: {
  state: EditorState;
  getCurrent: () => EditorState;
  dispatch: Dispatch<Action>;
}) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(false);
  const pending = useRef<{
    request_id: string;
    revision: string;
    draft: Draft;
  } | null>(null);
  const path = `/articles/${state.articleId}/metadata`;
  const history = useQuery({
    queryKey: ["metadata", state.articleId],
    queryFn: ({ signal }) => request<History>(path, undefined, signal),
    enabled: !!state.articleId,
    refetchInterval: 1500,
  });
  const turn = history.data?.turns.at(-1);
  const running = turn && ["queued", "running"].includes(turn.status);
  const proposal = turn?.result.replacement;
  const stale =
    !!turn && (turn.source_changed || !same(state.draft, turn.draft));
  const missing =
    !state.draft.summary.trim() ||
    !state.draft.author.trim() ||
    !state.draft.tags.some((t) => t.trim());
  async function generate() {
    if (active.current || !state.articleId || !history.data) return;
    active.current = true;
    setBusy(true);
    setError("");
    if (!pending.current)
      pending.current = {
        request_id: crypto.randomUUID().replaceAll("-", ""),
        revision: history.data.revision,
        draft: structuredClone(state.draft),
      };
    try {
      await request(path, pending.current);
      pending.current = null;
      await client.invalidateQueries({
        queryKey: ["metadata", state.articleId],
      });
      void client.invalidateQueries({ queryKey: ["tasks"] });
    } catch (e) {
      const failure = e as Error & { status?: number };
      if (failure.status && failure.status >= 400 && failure.status < 500)
        pending.current = null;
      setError(failure.message);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  async function apply() {
    const before = getCurrent();
    if (active.current || !turn || !proposal || stale || before.saving) return;
    active.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await request<Turn["result"]>(
        `${path}/${turn.id}/accept`,
        { draft: before.draft },
      );
      if (result.replacement)
        dispatch({
          type: "metadata_apply",
          session: before.session,
          sequence: before.sequence,
          before: before.draft,
          replacement: result.replacement,
        });
      await client.invalidateQueries({
        queryKey: ["metadata", before.articleId],
      });
      void client.invalidateQueries({ queryKey: ["tasks"] });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return (
    <section className="metadata-assistant" aria-label="AI 补齐文章信息">
      <div className="metadata-action">
        <strong>AI 补齐文章信息</strong>
        <button
          disabled={
            busy ||
            !!running ||
            !history.data ||
            !!state.saving ||
            state.draft.profile_id !== state.saved.profile_id ||
            !state.draft.body.trim() ||
            (!missing && !pending.current)
          }
          onClick={() => void generate()}
        >
          {running
            ? "正在补齐…"
            : pending.current
              ? "核对上次请求"
              : !missing
                ? "信息已完整"
                : stale
                  ? "按当前稿重新补齐"
                  : "AI 补齐空白信息"}
        </button>
      </div>
      <p>
        根据正文生成摘要和标签，署名沿用画像信息；已有内容保留。封面与来源请自行核实填写。
      </p>
      {!state.articleId && <p>先保存文章一次，再开始补齐。</p>}
      {state.draft.profile_id !== state.saved.profile_id && (
        <p>已更换画像，请先保存后再补齐。</p>
      )}
      {running && (
        <p role="status">
          {turn.status === "queued" ? "已排队" : "正在生成建议"}
          ，可以继续编辑，完成后在这里确认。
        </p>
      )}
      {(error || history.error || turn?.error) && (
        <p role="alert">{error || history.error?.message || turn?.error}</p>
      )}
      {proposal && (
        <details
          className="metadata-proposal"
          key={`${turn?.id}-${turn?.status}`}
          open={turn?.status === "review"}
        >
          <summary>
            {turn?.status === "accepted"
              ? "上次已取用的建议"
              : "补齐建议 · 尚未写入文章"}
          </summary>
          <dl>
            <dt>摘要</dt>
            <dd>{proposal.summary}</dd>
            <dt>作者署名</dt>
            <dd>
              {proposal.author || "暂时没有可核实的署名，请手动填写"}
              <small>来源：{turn?.result.author_source}</small>
            </dd>
            <dt>标签</dt>
            <dd>{proposal.tags.join("、") || "无建议"}</dd>
          </dl>
          {stale && turn?.status !== "accepted" && (
            <p>文章或画像已有变化，请按当前稿重新补齐。旧建议仍保留。</p>
          )}
          <button
            disabled={
              busy || stale || !!state.saving || turn?.status !== "review"
            }
            onClick={() => void apply()}
          >
            确认填入空白处
          </button>
          <small>填入后仍可修改，点击“保存文章”才保存。</small>
        </details>
      )}
    </section>
  );
}
