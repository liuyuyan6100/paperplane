import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";

type Profile = { id: string; name: string };
type Candidate = {
  title: string;
  angle: string;
  reader_value: string;
  profile_fit: string;
  evidence: string;
  gaps: string;
  difference: string;
};
type Plan = {
  id: string;
  status: string;
  error: string;
  idea: string;
  profile_name: string;
  profile_version: number;
  candidates: Candidate[];
  selected_index: number | null;
  selected_post_id: string | null;
  created_at: string;
  updated_at: string;
};
const labels: Record<string, string> = {
  queued: "排队中",
  running: "正在生成",
  completed: "待选择",
  failed: "生成失败",
  interrupted: "生成中断",
};
const newId = () => crypto.randomUUID().replaceAll("-", "");

export function TopicPanel({
  articleProfileId,
  profiles,
  onDirty,
  onOpenArticle,
}: {
  articleProfileId: string | null;
  profiles: Profile[];
  onDirty: (value: boolean) => void;
  onOpenArticle: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [profileId, setProfileId] = useState("");
  const [idea, setIdea] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const pending = useRef<string | null>(null);
  const busyRef = useRef(false);
  const client = useQueryClient();
  const history = useQuery({
    queryKey: ["topic-plans"],
    queryFn: () => request<{ plans: Plan[] }>("/topics"),
    enabled: open,
    refetchInterval: open ? 3000 : false,
  });
  const detail = useQuery({
    queryKey: ["topic-plan", selected],
    queryFn: () => request<Plan>("/topics/" + selected),
    enabled: open && !!selected,
    refetchInterval: open ? 2000 : false,
  });
  const plan = detail.data;

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => onDirty(!!idea.trim() || busy), [idea, busy, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  function openPanel() {
    setProfileId(articleProfileId || profiles[0]?.id || "");
    setOpen(true);
  }
  function closePanel() {
    if (busyRef.current) return;
    if (idea.trim() && !confirm("选题想法尚未提交，关闭会放弃输入，是否继续？")) return;
    setIdea("");
    pending.current = null;
    setOpen(false);
  }
  async function create() {
    if (busyRef.current || !profileId || !idea.trim()) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    pending.current ||= newId();
    try {
      const result = await request<Plan>("/topics", {
        request_id: pending.current,
        profile_id: profileId,
        idea,
      });
      pending.current = null;
      setIdea("");
      setSelected(result.id);
      client.setQueryData(["topic-plan", result.id], result);
      void client.invalidateQueries({ queryKey: ["topic-plans"] });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function retry() {
    if (!plan || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await request<Plan>("/topics/" + plan.id + "/retry", {});
      client.setQueryData(["topic-plan", plan.id], result);
      void client.invalidateQueries({ queryKey: ["topic-plans"] });
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }
  async function select(index: number) {
    if (!plan || busyRef.current) return;
    if (idea.trim() && !confirm("另有尚未提交的选题想法，选择候选后会放弃这段输入，是否继续？")) return;
    busyRef.current = true;
    setBusy(true);
    setError("");
    try {
      const result = await request<{ plan: Plan; post: { id: string } }>(
        "/topics/" + plan.id + "/select", { index },
      );
      client.setQueryData(["topic-plan", plan.id], result.plan);
      void client.invalidateQueries({ queryKey: ["topic-plans"] });
      void client.invalidateQueries({ queryKey: ["articles"] });
      setIdea("");
      pending.current = null;
      setOpen(false);
      onDirty(false);
      onOpenArticle(result.post.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }

  return <>
    <button onClick={openPanel}>帮我选题</button>
    <dialog ref={dialog} className="creation-panel topic-panel" aria-label="选题策划"
      onCancel={(event) => { event.preventDefault(); closePanel(); }}>
      <header>
        <div><h2>帮我选题</h2><p>结合一份画像和本次想法生成 3 个候选；选定后才新建文章。</p></div>
        <button onClick={closePanel} disabled={busy}>返回文章</button>
      </header>
      <div className="creation-content topic-content">
        <section className="topic-composer">
          <label>使用画像
            <select value={profileId} disabled={busy} onChange={(event) => {
              setProfileId(event.target.value); pending.current = null;
            }}>
              <option value="">请选择画像</option>
              {profiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
            </select>
          </label>
          <label>本次想法或素材摘要
            <textarea maxLength={12000} rows={5} value={idea} disabled={busy}
              placeholder="例如：最近验证了 Agent 记忆，想写一篇读者能照着复现的文章。"
              onChange={(event) => { setIdea(event.target.value); pending.current = null; }} />
          </label>
          <p>会参考该画像最近 30 篇文章的标题与摘要；链接只作线索，需要用到的事实请写入上方。点击生成会调用已配置的文字模型，可能产生费用。</p>
          <button className="primary" disabled={busy || !profileId || !idea.trim()} onClick={() => void create()}>生成 3 个候选选题</button>
          {!profiles.length && <p role="status">请先在文章信息区创建账号画像。</p>}
        </section>
        <section className="topic-history">
          <h3>之前的选题</h3>
          {history.error && <p role="alert" className="error">{history.error.message}</p>}
          {!history.data && !history.error && <p role="status">正在读取选题记录…</p>}
          {history.data?.plans.length === 0 && <p>还没有选题记录。</p>}
          {history.data?.plans.map((item) => {
            const shown = selected === item.id && plan ? plan : item;
            return <button key={item.id} className={selected === item.id ? "selected" : ""}
              onClick={() => { setSelected(item.id); setError(""); }}>
              <strong>{shown.idea.slice(0, 70) || "根据画像策划"}</strong>
              <small>{shown.profile_name} · {shown.selected_post_id ? "已选定" : labels[shown.status] || shown.status}</small>
            </button>;
          })}
        </section>
        <section className="topic-result">
          {!selected && <p>生成后可在这里比较候选，也能从历史记录继续查看。</p>}
          {selected && !plan && !detail.error && <p role="status">正在读取候选…</p>}
          {detail.error && <p role="alert" className="error">{detail.error.message}</p>}
          {plan && <>
            <h3>{plan.profile_name} · 画像 v{plan.profile_version}</h3>
            <p>{plan.idea}</p>
            <p role="status">{plan.selected_post_id ? "已选定并保存文章" : labels[plan.status] || plan.status}</p>
            {plan.error && <p role="alert" className="error">{plan.error}</p>}
            {(["failed", "interrupted"].includes(plan.status)) && <button disabled={busy} onClick={() => void retry()}>按原输入重试</button>}
            {(["queued", "running"].includes(plan.status)) && <p>任务继续运行，关闭窗口后仍可回来查看。</p>}
            {plan.selected_post_id && <button className="primary" onClick={() => {
              if (idea.trim() && !confirm("另有尚未提交的选题想法，打开文章会放弃这段输入，是否继续？")) return;
              setIdea(""); pending.current = null;
              setOpen(false); onDirty(false); onOpenArticle(plan.selected_post_id!);
            }}>打开已选文章</button>}
            <div className="topic-candidates">
              {plan.candidates.map((candidate, index) => <article key={index}>
                <small>候选 {index + 1}{plan.selected_index === index ? " · 已选定" : ""}</small>
                <h4>{candidate.title}</h4>
                <p><strong>切入角度：</strong>{candidate.angle}</p>
                <p><strong>读者收益：</strong>{candidate.reader_value}</p>
                <details><summary>查看依据、缺口与差异</summary>
                  <p><strong>账号匹配：</strong>{candidate.profile_fit}</p>
                  <p><strong>已有素材：</strong>{candidate.evidence}</p>
                  <p><strong>仍缺少：</strong>{candidate.gaps}</p>
                  <p><strong>与旧文区别：</strong>{candidate.difference}</p>
                </details>
                {!plan.selected_post_id && <button className="primary" disabled={busy} onClick={() => void select(index)}>选这个，创建选题文章</button>}
              </article>)}
            </div>
          </>}
          {error && <p role="alert" className="error">{error} 当前想法与记录仍保留，可核对后重试。</p>}
        </section>
      </div>
      <footer>模型只生成选题建议。选择一个候选后才创建空白选题文章；正文、提纲和发布仍按原步骤进行。</footer>
    </dialog>
  </>;
}
