import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { loadArticle, request } from "../../api";
import { isDirty, type Article, type EditorState } from "../editor/session";

export interface Workflow {
  id: string;
  post_id: string;
  post_title: string;
  post_version: number;
  status: string;
  stage: string;
  stage_label: string;
  instruction: string;
  reference_ids: string[];
  outline: string;
  outline_version: number;
  confirmed_outline_version: number;
  selected_title: string;
  result_signature: string;
  result: { title?: string; body?: string; review?: { warnings: string[] } };
  input_changes: string[];
  references: { id: string; title: string; version: number; source: string }[];
  error: string;
  created_at: string;
  applied_version: number | null;
}
type Material = {
  id: string;
  title: string;
  summary: string;
  source: string;
  version: number;
};
type Adoption = {
  version: number;
  result_signature: string;
  resolution: "unchanged" | "candidate" | "merged";
  content?: { title: string; body: string };
};
const statuses: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  outline_review: "待确认提纲",
  review: "正文待审",
  applied: "已采用",
  failed: "失败",
  interrupted: "已中断",
  canceled: "已取消",
};
const newId = () => crypto.randomUUID().replaceAll("-", "");

export function CreationPanel({
  state,
  getCurrent,
  onAdopt,
  onDirty,
}: {
  state: EditorState;
  getCurrent: () => EditorState;
  onAdopt: (id: string, values: Adoption) => Promise<void>;
  onDirty: (dirty: boolean) => void;
}) {
  const [open, setOpen] = useState(false),
    dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [instruction, setInstruction] = useState(
    "根据所选素材扩写正文，区分有依据的事实与待核实内容。",
  );
  const [references, setReferences] = useState<string[]>([]),
    [parent, setParent] = useState<Workflow | null>(null);
  const [search, setSearch] = useState(""),
    [sharedSearch, setSharedSearch] = useState<string | null>(null);
  const [materialTitle, setMaterialTitle] = useState(""),
    [materialBody, setMaterialBody] = useState(""),
    [source, setSource] = useState("");
  const [previewId, setPreviewId] = useState<string | null>(null),
    [detailDirty, setDetailDirty] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const active = useRef(false),
    pending = useRef<{ fingerprint: string; id: string } | null>(null);
  const client = useQueryClient();
  const id = state.articleId,
    base = `/articles/${id}/creation`;
  const materials = useQuery({
    queryKey: ["creation-materials"],
    queryFn: () => request<{ materials: Material[] }>("/creation/materials"),
    enabled: open,
  });
  const shared = useQuery({
    queryKey: ["creation-shared", sharedSearch],
    queryFn: () =>
      request<{
        items: { id: string; name: string; version: number; source: string }[];
      }>("/creation/shared?query=" + encodeURIComponent(sharedSearch ?? "")),
    enabled: open && sharedSearch !== null,
    retry: false,
  });
  const history = useQuery({
    queryKey: ["creation-history", id],
    queryFn: () => request<{ workflows: Workflow[] }>(base),
    enabled: open && !!id,
    refetchInterval: open ? 2000 : false,
  });
  const detail = useQuery({
    queryKey: ["creation-detail", id, selected],
    queryFn: () => request<Workflow>(base + "/" + selected),
    enabled: open && !!id && !!selected,
    refetchInterval: open ? 1500 : false,
  });
  const current = useQuery({
    queryKey: ["creation-current", id],
    queryFn: () => loadArticle(id!),
    enabled: open && !!id,
    refetchInterval: open ? 2000 : false,
  });
  const materialPreview = useQuery({
    queryKey: ["creation-material-preview", previewId],
    queryFn: () => loadArticle(previewId!),
    enabled: open && !!previewId,
  });
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  const composerDirty =
    references.length > 0 ||
    instruction !== "根据所选素材扩写正文，区分有依据的事实与待核实内容。";
  useEffect(() => {
    onDirty(
      detailDirty ||
        !!materialTitle ||
        !!materialBody ||
        !!source ||
        composerDirty,
    );
  }, [
    detailDirty,
    materialTitle,
    materialBody,
    source,
    composerDirty,
    onDirty,
  ]);
  useEffect(() => () => onDirty(false), [onDirty]);

  async function run<T>(work: () => Promise<T>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      return await work();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      active.current = false;
      setBusy(false);
      void client.invalidateQueries({ queryKey: ["creation-history", id] });
      void client.invalidateQueries({
        queryKey: ["creation-detail", id, selected],
      });
      void client.invalidateQueries({ queryKey: ["tasks"] });
    }
  }
  function choose(next: string | null) {
    if (
      detailDirty &&
      !confirm("待审稿或提纲有未保存编辑，切换会放弃这些编辑，是否继续？")
    )
      return;
    setDetailDirty(false);
    setSelected(next);
    setError("");
  }
  async function addMaterial(values: Record<string, unknown>) {
    const fingerprint = JSON.stringify(values);
    if (pending.current?.fingerprint !== fingerprint)
      pending.current = { fingerprint, id: newId() };
    const saved = await request<Article>("/creation/materials", {
      ...values,
      request_id: pending.current.id,
    });
    pending.current = null;
    setReferences((old) =>
      old.includes(saved.id) ? old : old.length < 6 ? [...old, saved.id] : old,
    );
    setMaterialTitle("");
    setMaterialBody("");
    setSource("");
    setNotice(
      "素材已保存到验证库；原文件和共享资料保持原样。最多选用6份素材。",
    );
    await client.invalidateQueries({ queryKey: ["creation-materials"] });
    void client.invalidateQueries({ queryKey: ["articles"] });
  }
  async function create(reuse = false, original = parent) {
    const editor = getCurrent();
    if (!editor.articleId || isDirty(editor) || editor.saving)
      throw new Error(
        "请先关闭此窗口并保存当前文章，再开始创作。已填的素材和要求会保留。",
      );
    const values = {
      version: editor.version,
      instruction: original && reuse ? original.instruction : instruction,
      reference_ids: original && reuse ? original.reference_ids : references,
      parent_id: original?.id ?? null,
      reuse_confirmed: reuse,
    };
    const fingerprint = JSON.stringify(values);
    if (pending.current?.fingerprint !== fingerprint)
      pending.current = { fingerprint, id: newId() };
    const task = await request<Workflow>(base, {
      ...values,
      request_id: pending.current.id,
    });
    pending.current = null;
    setSelected(task.id);
    setParent(null);
    setReferences([]);
    setInstruction("根据所选素材扩写正文，区分有依据的事实与待核实内容。");
    setNotice(
      task.status === "outline_review"
        ? "原提纲已保留，请按最新输入调整并重新确认。"
        : "任务已保存，可关闭窗口，稍后查看进度。",
    );
  }
  const task = detail.data;
  return (
    <>
      <button onClick={() => setOpen(true)}>素材与创作</button>
      <dialog
        ref={dialog}
        className="creation-panel"
        aria-labelledby="creation-heading"
        onCancel={(e) => {
          e.preventDefault();
          setOpen(false);
        }}
      >
        <header>
          <div>
            <small>M4 · 素材 → 提纲 → 正文采用</small>
            <h2 id="creation-heading">本篇创作</h2>
            <p>先确认提纲，再生成正文。待审稿采用后才保存到原文章。</p>
          </div>
          <button onClick={() => setOpen(false)}>返回文章</button>
        </header>
        <div className="creation-layout">
          <nav aria-label="创作历史">
            <button onClick={() => choose(null)}>＋ 准备一次创作</button>
            {history.data?.workflows.map((t) => (
              <button
                key={t.id}
                className={selected === t.id ? "selected" : ""}
                onClick={() => choose(t.id)}
              >
                <strong>{statuses[t.status] ?? t.status}</strong>
                <span>{t.instruction.slice(0, 70)}</span>
                <small>
                  {new Date(t.created_at).toLocaleString()} · 原稿v
                  {t.post_version}
                </small>
              </button>
            ))}
            {history.error && <p role="alert">{history.error.message}</p>}
            {!history.data?.workflows.length && (
              <p>本篇还没有创作任务，先选择素材并填写要求。</p>
            )}
          </nav>
          <main className="creation-content">
            {error && (
              <p role="alert" className="error">
                {error}
              </p>
            )}
            {notice && (
              <p role="status" className="notice">
                {notice}
              </p>
            )}
            {selected ? (
              <>
                {detail.error && (
                  <p role="alert">
                    {detail.error.message}
                    <button onClick={() => void detail.refetch()}>
                      重新读取任务
                    </button>
                  </p>
                )}
                {task && task.id === selected && (
                  <WorkflowView
                    key={task.id}
                    task={task}
                    current={current.data}
                    editor={state}
                    busy={busy}
                    onDirty={setDetailDirty}
                    onChange={(action, values) =>
                      run(async () => {
                        const updated = await request<Workflow>(
                          base + "/" + task.id + "/" + action,
                          values,
                        );
                        client.setQueryData(
                          ["creation-detail", id, selected],
                          updated,
                        );
                        setNotice(
                          action === "confirm"
                            ? "提纲已确认，正文正在排队生成。"
                            : "修改已保存到本任务，原文章保持不变。",
                        );
                        return updated;
                      })
                    }
                    onAdopt={(values) =>
                      run(async () => {
                        await onAdopt(task.id, values);
                        setDetailDirty(false);
                        await current.refetch();
                        await detail.refetch();
                        setNotice(
                          "正文已采用并保存为文章新版本，原有修订继续保留。",
                        );
                      })
                    }
                    onRevise={() => {
                      if (
                        detailDirty &&
                        !confirm("先放弃本地未保存编辑，再调整素材和要求？")
                      )
                        return;
                      setDetailDirty(false);
                      setSelected(null);
                      setParent(task);
                      setReferences(task.reference_ids);
                      setInstruction(task.instruction);
                    }}
                    onKeep={() => setOpen(false)}
                    onRegenerate={() => void run(() => create(true, task))}
                  />
                )}
              </>
            ) : (
              <>
                <h3>1 · 素材与写作要求</h3>
                {(!id || isDirty(state)) && (
                  <p className="warning">
                    先返回文章并保存当前稿，再开始生成；这里填写的内容会保留。
                  </p>
                )}
                {parent && (
                  <p className="notice">
                    基于已有任务调整。保留原提纲，重新确认后才生成正文。
                    <button onClick={() => setParent(null)}>
                      改为生成新提纲
                    </button>
                  </p>
                )}
                <label>
                  写作要求
                  <textarea
                    aria-label="创作要求"
                    disabled={busy}
                    value={instruction}
                    maxLength={4000}
                    rows={4}
                    onChange={(e) => setInstruction(e.target.value)}
                  />
                </label>
                <label>
                  搜索已有素材
                  <input
                    aria-label="搜索已有素材"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="文章或笔记标题、摘要"
                  />
                </label>
                <p>
                  已选 {references.length}
                  /6。只使用当前已保存文章和明确勾选的素材，任务保留各自版本快照。
                </p>
                {references.length > 0 && (
                  <button disabled={busy} onClick={() => setReferences([])}>
                    清空素材选择
                  </button>
                )}
                <div className="material-list">
                  {materials.data?.materials
                    .filter(
                      (m) =>
                        m.id !== id &&
                        ((m.title + m.summary).includes(search) ||
                          references.includes(m.id)),
                    )
                    .map((m) => (
                      <div key={m.id}>
                        <label>
                          <input
                            type="checkbox"
                            checked={references.includes(m.id)}
                            disabled={
                              busy ||
                              (!references.includes(m.id) &&
                                references.length >= 6)
                            }
                            onChange={(e) =>
                              setReferences((old) =>
                                e.target.checked
                                  ? [...old, m.id]
                                  : old.filter((x) => x !== m.id),
                              )
                            }
                          />
                          <span>
                            {m.title}
                            <small>
                              v{m.version} · {m.source || "本机素材"}
                            </small>
                          </span>
                        </label>
                        <button
                          onClick={() =>
                            setPreviewId(previewId === m.id ? null : m.id)
                          }
                        >
                          查看素材
                        </button>
                      </div>
                    ))}
                </div>
                {materialPreview.data && previewId && (
                  <details open>
                    <summary>{materialPreview.data.title} · 素材原文</summary>
                    <pre className="material-text">
                      {materialPreview.data.body}
                    </pre>
                  </details>
                )}
                {materialPreview.error && (
                  <p role="alert">
                    素材读取失败：{materialPreview.error.message}
                  </p>
                )}
                <details>
                  <summary>新增文字素材 / 导入文件</summary>
                  <p>
                    可粘贴文字，或读取 UTF-8 的 .md / .txt /
                    .csv。先保存素材，再用于生成。
                  </p>
                  <input
                    type="file"
                    aria-label="导入文字素材文件"
                    disabled={busy}
                    accept=".md,.txt,.csv"
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      if (!file) return;
                      void run(async () => {
                        if (file.size > 800000)
                          throw new Error("文字素材过大，请拆分后导入。");
                        const text = new TextDecoder("utf-8", {
                          fatal: true,
                        }).decode(await file.arrayBuffer());
                        if (text.length > 200000)
                          throw new Error("单份文字素材不能超过20万字。");
                        setMaterialTitle(
                          file.name.replace(/\.[^.]+$/, "").slice(0, 150),
                        );
                        setMaterialBody(text);
                        setSource("本地文件：" + file.name);
                      });
                      e.target.value = "";
                    }}
                  />
                  <label>
                    素材标题
                    <input
                      aria-label="素材标题"
                      disabled={busy}
                      value={materialTitle}
                      maxLength={150}
                      onChange={(e) => setMaterialTitle(e.target.value)}
                    />
                  </label>
                  <label>
                    素材来源
                    <input
                      aria-label="素材来源"
                      disabled={busy}
                      value={source}
                      maxLength={2000}
                      onChange={(e) => setSource(e.target.value)}
                    />
                  </label>
                  <label>
                    素材内容
                    <textarea
                      aria-label="素材内容"
                      disabled={busy}
                      rows={7}
                      value={materialBody}
                      maxLength={200000}
                      onChange={(e) => setMaterialBody(e.target.value)}
                    />
                  </label>
                  <button
                    disabled={
                      busy || !materialTitle.trim() || !materialBody.trim()
                    }
                    onClick={() =>
                      void run(() =>
                        addMaterial({
                          title: materialTitle,
                          body: materialBody,
                          source,
                        }),
                      )
                    }
                  >
                    保存素材并选用
                  </button>
                </details>
                <details>
                  <summary>从共享资料库选择</summary>
                  <p>
                    文字资料导入一份本机素材；原资料不修改。其他文件继续从原工作台使用。
                  </p>
                  <button
                    disabled={busy}
                    onClick={() => {
                      setSharedSearch(search);
                      void shared.refetch();
                    }}
                  >
                    读取共享资料
                  </button>
                  {shared.error && <p role="alert">{shared.error.message}</p>}
                  {shared.data?.items.map((item) => (
                    <div className="shared-item" key={item.id}>
                      <span>
                        {item.name} · v{item.version} · {item.source}
                      </span>
                      <button
                        disabled={busy}
                        onClick={() =>
                          void run(() =>
                            addMaterial({
                              shared_id: item.id,
                              shared_version: item.version,
                            }),
                          )
                        }
                      >
                        导入并选用
                      </button>
                    </div>
                  ))}
                  {shared.data && !shared.data.items.length && (
                    <p>此范围没有可导入的文字素材。</p>
                  )}
                </details>
                <button
                  className="primary"
                  disabled={
                    busy ||
                    !id ||
                    isDirty(state) ||
                    !!state.saving ||
                    !instruction.trim()
                  }
                  onClick={() => void run(() => create())}
                >
                  {busy
                    ? "正在提交…"
                    : parent
                      ? "使用最新输入并保留提纲"
                      : "生成提纲"}
                </button>
              </>
            )}
          </main>
        </div>
        <footer>
          关闭此窗口不会停止任务。原文章、素材和历史继续保留；查看与采用不发起送稿。
        </footer>
      </dialog>
    </>
  );
}

function WorkflowView({
  task,
  current,
  editor,
  busy,
  onDirty,
  onChange,
  onAdopt,
  onRevise,
  onRegenerate,
  onKeep,
}: {
  task: Workflow;
  current?: Article;
  editor: EditorState;
  busy: boolean;
  onDirty: (v: boolean) => void;
  onChange: (action: string, values: unknown) => Promise<Workflow | undefined>;
  onAdopt: (v: Adoption) => Promise<void>;
  onRevise: () => void;
  onRegenerate: () => void;
  onKeep: () => void;
}) {
  const [outline, setOutline] = useState(task.outline),
    [title, setTitle] = useState(task.selected_title);
  const [outlineBase, setOutlineBase] = useState({
    outline: task.outline,
    title: task.selected_title,
    version: task.outline_version,
  });
  const [body, setBody] = useState(task.result.body ?? ""),
    [bodyTitle, setBodyTitle] = useState(
      task.result.title ?? task.selected_title,
    );
  const [bodyBase, setBodyBase] = useState({
    body: task.result.body ?? "",
    title: task.result.title ?? task.selected_title,
    signature: task.result_signature,
  });
  const [resolution, setResolution] = useState<"candidate" | "merged">(
    "candidate",
  );
  const [mergeBody, setMergeBody] = useState(""),
    [mergeTitle, setMergeTitle] = useState("");
  const editing = useRef({ outline, title, body, bodyTitle });
  editing.current = { outline, title, body, bodyTitle };
  const outlineDirty =
    outline !== outlineBase.outline || title !== outlineBase.title;
  const bodyDirty = body !== bodyBase.body || bodyTitle !== bodyBase.title;
  const dirty =
    outlineDirty ||
    bodyDirty ||
    (task.status === "review" && resolution === "merged");
  useEffect(() => {
    onDirty(dirty);
    return () => onDirty(false);
  }, [dirty, onDirty]);
  useEffect(() => {
    if (!outlineBase.outline && task.outline && !outlineDirty) {
      setOutline(task.outline);
      setTitle(task.selected_title);
      setOutlineBase({
        outline: task.outline,
        title: task.selected_title,
        version: task.outline_version,
      });
    }
  }, [
    task.outline,
    task.outline_version,
    task.selected_title,
    outlineBase.outline,
    outlineDirty,
  ]);
  useEffect(() => {
    if (!bodyBase.body && task.result.body && !bodyDirty) {
      setBody(task.result.body);
      setBodyTitle(task.result.title ?? task.selected_title);
      setBodyBase({
        body: task.result.body,
        title: task.result.title ?? task.selected_title,
        signature: task.result_signature,
      });
    }
  }, [
    task.result.body,
    task.result.title,
    task.result_signature,
    task.selected_title,
    bodyBase.body,
    bodyDirty,
  ]);
  const currentChanged = !!current && current.version !== task.post_version;
  const blocked =
    !current || isDirty(editor) || !!editor.saving || editor.loading;
  const shownBody = task.status === "applied" ? (task.result.body ?? "") : body;
  const shownTitle =
    task.status === "applied"
      ? (task.result.title ?? task.selected_title)
      : bodyTitle;
  const preview = useQuery({
    queryKey: ["creation-reading", current?.body, shownBody],
    queryFn: async ({ signal }) => {
      const [before, after] = await Promise.all([
        request<{ html: string }>(
          "/preview",
          { body: current?.body ?? "" },
          signal,
        ),
        request<{ html: string }>("/preview", { body: shownBody }, signal),
      ]);
      return { before: before.html, after: after.html };
    },
    enabled: ["review", "applied"].includes(task.status),
  });
  async function saveOutline(confirm = false) {
    const updated = await onChange(confirm ? "confirm" : "outline", {
      outline,
      selected_title: title,
      outline_version: outlineBase.version,
    });
    if (updated) {
      setOutlineBase({
        outline: updated.outline,
        title: updated.selected_title,
        version: updated.outline_version,
      });
      if (
        editing.current.outline === outline &&
        editing.current.title === title
      ) {
        setOutline(updated.outline);
        setTitle(updated.selected_title);
      }
    }
  }
  async function saveBody() {
    const updated = await onChange("body", {
      result_signature: bodyBase.signature,
      content: { title: bodyTitle, body },
    });
    if (updated) {
      setBodyBase({
        body: updated.result.body ?? "",
        title: updated.result.title ?? "",
        signature: updated.result_signature,
      });
      if (
        editing.current.body === body &&
        editing.current.bodyTitle === bodyTitle
      ) {
        setBody(updated.result.body ?? "");
        setBodyTitle(updated.result.title ?? "");
      }
    }
  }
  const candidateChanged = task.result_signature !== bodyBase.signature;
  return (
    <section className="workflow-view" aria-label="本篇创作详情">
      <div className="workflow-heading">
        <h3>{statuses[task.status] ?? task.status}</h3>
        <small>
          {task.id} · 原稿 v{task.post_version}
        </small>
      </div>
      <ol className="creation-steps">
        <li>1 素材快照已保存</li>
        <li
          aria-current={task.status === "outline_review" ? "step" : undefined}
        >
          2 编辑并确认提纲
        </li>
        <li aria-current={task.status === "review" ? "step" : undefined}>
          3 正文审阅与采用
        </li>
      </ol>
      <details>
        <summary>本次要求与素材版本</summary>
        <p className="preserve">{task.instruction}</p>
        {task.references.map((r) => (
          <p key={r.id}>
            {r.title} · v{r.version} · {r.source || "本机素材"}
          </p>
        ))}
      </details>
      {task.input_changes.length > 0 && task.status !== "applied" && (
        <p className="warning">
          {task.input_changes.join("、")}
          已变化。本次结果仍使用启动时的旧输入；调整素材和要求后可保留提纲重新确认。
        </p>
      )}
      {task.error && (
        <p role="alert" className="error">
          {task.error}
        </p>
      )}
      {task.status === "outline_review" && (
        <>
          <label>
            本篇采用标题
            <input
              aria-label="提纲采用标题"
              disabled={busy}
              maxLength={150}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            编辑提纲
            <textarea
              aria-label="编辑提纲"
              disabled={busy}
              rows={14}
              value={outline}
              maxLength={30000}
              onChange={(e) => setOutline(e.target.value)}
            />
          </label>
          <p>
            {outlineDirty ? "提纲有未保存修改。" : "提纲已保存在本任务。"}
            确认前不会生成正文。
          </p>
          <div className="creation-actions">
            <button
              disabled={busy || !outline.trim() || !title.trim()}
              onClick={() => void saveOutline()}
            >
              保存提纲
            </button>
            <button
              className="primary"
              disabled={
                busy ||
                blocked ||
                !outline.trim() ||
                !title.trim() ||
                task.input_changes.length > 0
              }
              onClick={() => void saveOutline(true)}
            >
              确认提纲并生成正文
            </button>
          </div>
          {blocked && (
            <p className="warning">请先返回文章保存当前修改，再确认提纲。</p>
          )}
          {task.outline_version !== outlineBase.version && (
            <p className="warning">
              提纲已在其他页面变化。
              <button
                onClick={() => {
                  if (
                    outlineDirty &&
                    !confirm("放弃本地提纲编辑并读取新版本？")
                  )
                    return;
                  setOutline(task.outline);
                  setTitle(task.selected_title);
                  setOutlineBase({
                    outline: task.outline,
                    title: task.selected_title,
                    version: task.outline_version,
                  });
                }}
              >
                读取最新提纲
              </button>
            </p>
          )}
        </>
      )}
      {task.status !== "outline_review" && outlineDirty && (
        <details>
          <summary>保留的未保存提纲编辑</summary>
          <p>
            任务已进入其他阶段，这份本地编辑仍保留，可复制或用于调整下一次提纲。
          </p>
          <pre className="preserve">{outline}</pre>
        </details>
      )}
      {["queued", "running"].includes(task.status) && (
        <p role="status">
          {task.stage_label}… 可以关闭窗口继续编辑，任务不会因此重复派发。
        </p>
      )}
      {["failed", "interrupted", "canceled"].includes(task.status) && (
        <p>
          原输入和已完成内容仍保留。可在任务中心按原编号恢复，或调整输入创建新的创作任务。
        </p>
      )}
      {["review", "applied"].includes(task.status) && (
        <>
          <details>
            <summary>查看已确认提纲</summary>
            <pre className="preserve">{task.outline}</pre>
          </details>
          {task.result.review?.warnings.map((w, i) => (
            <p className="creation-hint" key={i}>
              {w}
            </p>
          ))}
          {task.status === "review" && (
            <>
              <label>
                待审稿标题
                <input
                  aria-label="待审稿标题"
                  disabled={busy}
                  value={bodyTitle}
                  maxLength={150}
                  onChange={(e) => setBodyTitle(e.target.value)}
                />
              </label>
              <label>
                修改待审稿
                <textarea
                  aria-label="修改待审稿"
                  disabled={busy}
                  value={body}
                  rows={12}
                  maxLength={200000}
                  onChange={(e) => setBody(e.target.value)}
                />
              </label>
              <button
                disabled={
                  busy || !body.trim() || !bodyTitle.trim() || !bodyDirty
                }
                onClick={() => void saveBody()}
              >
                保存待审稿修改
              </button>
            </>
          )}
          <h3>当前文章与待审稿对照</h3>
          {preview.error && (
            <p role="alert">对照读取失败：{preview.error.message}</p>
          )}
          <div className="creation-compare">
            <section aria-label="当前文章对照">
              <h4>当前文章 · v{current?.version ?? "…"}</h4>
              <h3>{current?.title}</h3>
              <article
                className="prose"
                dangerouslySetInnerHTML={{ __html: preview.data?.before ?? "" }}
              />
            </section>
            <section aria-label="待审稿对照">
              <h4>{task.status === "applied" ? "本次已采用稿" : "待审稿"}</h4>
              <h3>{shownTitle}</h3>
              <article
                className="prose"
                dangerouslySetInnerHTML={{ __html: preview.data?.after ?? "" }}
              />
            </section>
          </div>
          {task.status === "review" && (
            <>
              {currentChanged && (
                <p className="warning">
                  生成期间原稿已从 v{task.post_version} 变为 v{current?.version}
                  。先对照两份内容，再明确选择；原修订继续保留。
                </p>
              )}
              <fieldset>
                <legend>审阅后的处理方式</legend>
                <button onClick={onKeep}>保留当前稿，暂不采用</button>
                <label>
                  <input
                    type="radio"
                    name="creation-resolution"
                    checked={resolution === "candidate"}
                    onChange={() => setResolution("candidate")}
                  />
                  使用待审稿
                </label>
                <label>
                  <input
                    type="radio"
                    name="creation-resolution"
                    checked={resolution === "merged"}
                    onChange={() => {
                      setResolution("merged");
                      setMergeBody(body);
                      setMergeTitle(bodyTitle);
                    }}
                  />
                  手动合并后采用
                </label>
                {resolution === "merged" && (
                  <>
                    <label>
                      合并后标题
                      <input
                        aria-label="合并后标题"
                        disabled={busy}
                        value={mergeTitle}
                        maxLength={150}
                        onChange={(e) => setMergeTitle(e.target.value)}
                      />
                    </label>
                    <label>
                      合并后正文
                      <textarea
                        aria-label="合并后正文"
                        disabled={busy}
                        rows={12}
                        maxLength={200000}
                        value={mergeBody}
                        onChange={(e) => setMergeBody(e.target.value)}
                      />
                    </label>
                  </>
                )}
              </fieldset>
              {blocked && (
                <p className="warning">
                  请先返回文章保存当前修改，再回来对照采用。输入与待审稿保留。
                </p>
              )}
              {bodyDirty && (
                <p className="warning">先保存待审稿修改，再确认采用。</p>
              )}
              {candidateChanged && (
                <p className="warning">
                  待审稿已在其他页面变化，本地文字保留。
                  <button
                    onClick={() => {
                      if (dirty && !confirm("放弃本地待审稿编辑并读取新版本？"))
                        return;
                      setBody(task.result.body ?? "");
                      setBodyTitle(task.result.title ?? "");
                      setBodyBase({
                        body: task.result.body ?? "",
                        title: task.result.title ?? "",
                        signature: task.result_signature,
                      });
                    }}
                  >
                    读取最新待审稿
                  </button>
                </p>
              )}
              <button
                className="primary"
                disabled={
                  busy ||
                  blocked ||
                  bodyDirty ||
                  candidateChanged ||
                  !preview.data ||
                  (resolution === "merged" &&
                    (!mergeTitle.trim() || !mergeBody.trim()))
                }
                onClick={() =>
                  void onAdopt({
                    version: current!.version,
                    result_signature: bodyBase.signature,
                    resolution:
                      resolution === "merged"
                        ? "merged"
                        : currentChanged
                          ? "candidate"
                          : "unchanged",
                    ...(resolution === "merged"
                      ? { content: { title: mergeTitle, body: mergeBody } }
                      : {}),
                  })
                }
              >
                确认采用并保存文章
              </button>
            </>
          )}
          {task.status === "applied" && (
            <p className="notice">
              本次正文已采用为文章 v{task.applied_version}
              。之后的文章修改不影响此历史记录。
            </p>
          )}
          <button disabled={busy || dirty || blocked} onClick={onRegenerate}>
            沿用已确认提纲重写
          </button>
        </>
      )}
      <button disabled={busy} onClick={onRevise}>
        调整素材和要求
      </button>
    </section>
  );
}
