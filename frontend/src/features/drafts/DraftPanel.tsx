import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { request } from "../../api";
import { isDirty, type Article, type EditorState } from "../editor/session";
import "./drafts.css";

type Check = { label: string; level: string; detail: string };
export type DraftView = {
  post: Article & { archived: boolean };
  report: { ok: boolean; checks: Check[] };
  intent: null | { status: string; checked_version: number; report: { checks?: Check[] } };
  job: null | { id: string; status: string; version: number; error: string; note: string; external_id?: string };
  preparation: null | { id: string; status: string; source_changed: boolean; error: string; result: {
    summary?: string; author?: string; author_source?: string; body?: string; pending?: string[];
    image_repairs?: { old: string; new: string; reason: string }[];
  } };
};
export type DraftCommand = "start" | "cancel" | "prepare" | "confirm";
const statuses: Record<string, string> = { queued: "等待执行", running: "正在处理", draft: "已送入公众号草稿箱", uncertain: "结果待核对", failed: "执行失败", published: "已发表", manual: "需要手动处理", canceled: "已取消", review: "建议待审阅", applied: "已采用", stale: "建议已过期", interrupted: "处理已中断" };

export function DraftPanel({ state, openRequest, onCommand, onEdit, onImages, onSettings, onTasks, onReload }: {
  state: EditorState; openRequest: number;
  onCommand: (action: DraftCommand, version: number, preparationId?: string) => Promise<DraftView>;
  onEdit: () => void; onImages: () => void; onSettings: () => void; onTasks: () => void; onReload: () => void;
}) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const firstRequest = useRef(openRequest);
  useEffect(() => { if (openRequest !== firstRequest.current) { firstRequest.current = openRequest; setOpen(true); } }, [openRequest]);
  useEffect(() => { if (open) dialog.current?.showModal(); else dialog.current?.close(); }, [open]);
  const query = useQuery({ queryKey: ["article-draft", state.articleId],
    queryFn: () => request<DraftView>(`/articles/${state.articleId}/draft`),
    enabled: open && !!state.articleId && !busy, refetchInterval: open && !busy ? 3000 : false });
  const data = query.data, preparation = data?.preparation, job = data?.job;
  const waiting = data?.intent?.status === "waiting";
  const mismatch = !!data && data.post.version !== state.version;
  const disabled = busy || !!state.saving || state.loading || isDirty(state) || mismatch || !data || !!data.post.archived || data.post.stage === "published" || !!query.error;
  async function command(action: DraftCommand) {
    if (disabled || !data) return;
    setBusy(true); setError("");
    try { await onCommand(action, data.post.version, preparation?.id); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); void query.refetch(); }
  }
  function leave(action: () => void) { setOpen(false); action(); }
  return <>
    <button onClick={() => setOpen(true)}>检查与公众号草稿</button>
    {createPortal(<dialog className="draft-panel" aria-label="检查与公众号草稿" ref={dialog} onCancel={(e) => { e.preventDefault(); if (!busy) setOpen(false); }}>
      <header><div><span className="milestone">图文 → 检查 → 公众号草稿</span><h2>检查与公众号草稿</h2>
        <p>查看不会送稿。启动后，补齐问题并保存会自动复查，检查通过后继续送入草稿箱。</p></div>
        <button disabled={busy} onClick={() => setOpen(false)}>返回文章</button></header>
      <div className="draft-panel-body">
        {!state.articleId ? <p role="status">请先保存文章，再检查图文。</p> : <>
          <div className="draft-actions"><button disabled={busy || query.isFetching} onClick={() => void query.refetch()}>刷新检查</button>
            <button disabled={busy} onClick={() => leave(onEdit)}>补齐文章信息</button>
            <button disabled={busy} onClick={() => leave(onImages)}>处理封面与配图</button>
            <button disabled={busy} onClick={() => leave(onSettings)}>公众号设置（原工作台）</button></div>
          {(error || query.error) && <p role="alert" className="draft-error">{error || query.error?.message}</p>}
          {isDirty(state) && <p className="draft-notice">编辑区有未保存修改。检查使用已保存版本，请先返回文章保存。</p>}
          {mismatch && <p className="draft-notice">已保存文章更新至 v{data?.post.version}，当前编辑区为 v{state.version}。
            <button disabled={busy} onClick={() => leave(onReload)}>载入最新已保存版本</button></p>}
          {!data && !query.error && <p role="status">正在读取检查结果…</p>}
          {data && <>
            <h3>{data.post.title} · 已保存 v{data.post.version}</h3>
            {job ? <section className="draft-receipt" aria-label="草稿结果"><h3>{statuses[job.status] || job.status}</h3>
              <p>送稿版本 v{job.version} · 任务 {job.id}</p>
              {job.version !== data.post.version && <p>文章已有新版本。此记录对应旧版本；本轮尚不自动更新微信旧草稿。</p>}
              {job.external_id && <p>草稿回执：{job.external_id}</p>}
              {job.note && <p>{job.note}</p>}{job.error && <p className="draft-error">{job.error}</p>}
              {job.status === "uncertain" && <p>请先核对微信草稿箱结果。核对前不重新发送。</p>}
              <button onClick={() => leave(onTasks)}>在任务中心处理或核对</button>
            </section> : <section className="draft-receipt">
              {waiting ? <><h3>等待补齐 · 保存后自动继续</h3><p>不再需要本次送稿时，可以停止等待；文章和已有建议保留。</p>
                <button disabled={disabled} onClick={() => void command("cancel")}>停止本次送稿等待</button></> : <>
                <h3>{data.intent?.status === "canceled" ? "已停止等待" : "尚未启动送稿"}</h3>
                <p>启动会整理并保存确定的格式问题，将文章设为待发布；缺少摘要时可能调用文字模型生成待审建议，产生模型费用。只送入草稿箱。</p>
                <button className="primary" disabled={disabled} onClick={() => void command("start")}>检查并送入公众号草稿箱</button></>}
            </section>}
            {waiting && data.intent?.report.checks?.some((c) => c.label === "送稿准备") && <div className="draft-notice"><p role="alert">{data.intent.report.checks.filter((c) => c.label === "送稿准备").map((c) => c.detail).join("；")}</p><button disabled={disabled} onClick={() => void command("start")}>重新检查并继续</button></div>}
            {waiting && !job && preparation && <section aria-label="补齐建议" className="draft-suggestion">
              <h3>补齐建议 · {statuses[preparation.status] || preparation.status}</h3>
              {preparation.error && <p role="alert">{preparation.error}</p>}
              {preparation.source_changed ? <p>文章或素材已经变化，请重新准备建议。</p> : preparation.status === "review" && <>
                <dl><dt>摘要</dt><dd>{preparation.result.summary || "尚缺摘要"}</dd><dt>作者</dt><dd>{preparation.result.author || "请返回文章填写作者"}</dd>
                  <dt>署名依据</dt><dd>{preparation.result.author_source}</dd></dl>
                {preparation.result.image_repairs?.map((r, i) => <p key={i}>{r.reason}：{r.old} → {r.new}</p>)}
                {preparation.result.pending?.map((item, i) => <p key={i} className="draft-notice">{item}</p>)}
                <details><summary>核对采用后的正文（包含图文引用与文末署名）</summary><pre>{preparation.result.body}</pre></details>
                <button className="primary" disabled={disabled || !preparation.result.author || !preparation.result.summary} onClick={() => void command("confirm")}>采用补齐建议并继续检查</button>
              </>}
              {(preparation.source_changed || ["failed", "interrupted", "stale", "canceled"].includes(preparation.status)) &&
                <button disabled={disabled} onClick={() => void command("prepare")}>重新准备补齐建议</button>}
            </section>}
            <section aria-label="图文检查结果">{job && <p>下方为当前文章的送稿条件检查，重复检查阻止再次发送，不改变上方已有回执。</p>}<h3>当前版本检查 · {data.report.ok ? "通过" : "有问题需处理"}</h3>
              <ul className="draft-checks">{[...data.report.checks].sort((a, b) => ({ error: 0, warn: 1, pass: 2 }[a.level] ?? 3) - ({ error: 0, warn: 1, pass: 2 }[b.level] ?? 3)).map((check, i) => <li key={i} className={"check-" + check.level}>
                <span>{check.level === "pass" ? "✓" : check.level === "error" ? "!" : "△"}</span><div><strong>{check.label}</strong><p>{check.detail}</p></div></li>)}</ul>
            </section>
          </>}
        </>}
      </div>
      <footer>关闭面板不会停止已启动的流程。草稿入箱不代表公开发表。</footer>
    </dialog>, document.body)}
  </>;
}
