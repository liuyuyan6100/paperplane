import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";
import { isDirty, type EditorState } from "../editor/session";
import {
  editablePages,
  imageStatus,
  type ImageAdoption,
  type ImagePage,
  type ImagePreview,
  type Plan,
} from "./types";

const newId = () => crypto.randomUUID().replaceAll("-", "");
type Props = {
  state: EditorState;
  getCurrent: () => EditorState;
  onDirty: (dirty: boolean, areas?: string[]) => void;
  onAdopt: (id: string, values: ImageAdoption) => Promise<void>;
};

export function IllustrationPanel({
  state,
  getCurrent,
  onDirty,
  onAdopt,
}: Props) {
  const [open, setOpen] = useState(false),
    dialog = useRef<HTMLDialogElement>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [archived, setArchived] = useState(false),
    [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const active = useRef(false),
    pending = useRef<{ fingerprint: string; id: string } | null>(null);
  const client = useQueryClient(),
    id = state.articleId,
    base = `/articles/${id}/illustrations`;
  const history = useQuery({
    queryKey: ["illustration-history", id, archived],
    queryFn: () =>
      request<{
        plans: {
          id: string;
          version: number;
          status: string;
          created_at: string;
        }[];
      }>(base + `?archived=${archived}`),
    enabled: open && !!id,
    refetchInterval: open ? 2000 : false,
  });
  const detail = useQuery({
    queryKey: ["illustration-detail", id, selected],
    queryFn: () => request<Plan>(base + "/" + selected),
    enabled: open && !!id && !!selected,
    refetchInterval: open ? 1500 : false,
  });
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    onDirty(dirty, dirty ? ["封面与配图：尚未保存的计划、图注或修改要求"] : []);
  }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  async function run(work: () => Promise<unknown>) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      active.current = false;
      setBusy(false);
      void client.invalidateQueries({ queryKey: ["illustration-history", id] });
      void client.invalidateQueries({
        queryKey: ["illustration-detail", id, selected],
      });
      void client.invalidateQueries({ queryKey: ["tasks"] });
    }
  }
  function choose(value: string | null, archiveView = archived) {
    if (dirty && !confirm("配图有未保存输入，切换将放弃这些输入，是否继续？"))
      return;
    setDirty(false);
    setSelected(value);
    setArchived(archiveView);
    setError("");
    setNotice("");
  }
  function requireSaved() {
    const editor = getCurrent();
    if (!editor.articleId || isDirty(editor) || editor.saving || editor.loading)
      throw new Error(
        "请先返回文章并保存当前稿，再建立计划或采用图片。配图输入会保留。",
      );
    return editor;
  }
  async function identified<T>(url: string, values: object): Promise<T> {
    const fingerprint = JSON.stringify([url, values]);
    if (pending.current?.fingerprint !== fingerprint)
      pending.current = { fingerprint, id: newId() };
    const result = await request<T>(url, {
      ...values,
      request_id: pending.current.id,
    });
    pending.current = null;
    return result;
  }
  async function change(
    action: string,
    values: object,
    identifiedRequest = false,
  ) {
    const path = base + "/" + selected + "/" + action;
    const result = identifiedRequest
      ? await identified<Plan>(path, values)
      : await request<Plan>(path, values);
    client.setQueryData(["illustration-detail", id, selected], result);
    return result;
  }
  const plan = detail.data;
  return (
    <>
      <button onClick={() => setOpen(true)}>封面与配图</button>
      {dirty && (
        <span className="dirty" role="status">
          配图有未保存输入
        </span>
      )}
      <dialog
        ref={dialog}
        className="creation-panel image-panel"
        aria-label="本篇封面与配图"
        onCancel={(e) => {
          e.preventDefault();
          setOpen(false);
        }}
      >
        <header>
          <div>
            <h2>本篇封面与配图</h2>
            <p>① 准备计划 ② 逐张生成或上传 ③ 预览位置 ④ 采用并保存</p>
          </div>
          <button onClick={() => setOpen(false)}>返回文章</button>
        </header>
        {!id ? (
          <div className="creation-content">
            <p>请先填写标题与正文，保存文章，再准备封面和正文插图。</p>
          </div>
        ) : (
          <div className="creation-layout">
            <nav aria-label="配图历史">
              <button disabled={busy} onClick={() => choose(null, false)}>
                ＋ 新建配图计划
              </button>
              <button disabled={busy} onClick={() => choose(null, !archived)}>
                {archived ? "查看当前计划" : "查看归档计划"}
              </button>
              {history.error && <p role="alert">{history.error.message}</p>}
              {history.data?.plans.map((p) => (
                <button
                  key={p.id}
                  aria-current={selected === p.id ? "true" : undefined}
                  disabled={busy}
                  onClick={() => choose(p.id)}
                >
                  <strong>{p.created_at.slice(0, 16).replace("T", " ")}</strong>
                  <small>
                    {imageStatus[p.status] ?? p.status} · 计划 v{p.version} ·{" "}
                    {p.id.slice(0, 8)}
                  </small>
                </button>
              ))}
            </nav>
            <section className="creation-content" aria-label="配图详情">
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
              {!selected ? (
                <>
                  <h3>{archived ? "已归档计划" : "从已保存正文建立计划"}</h3>
                  <p>
                    保留正文、已确认提纲、画像视觉风格与真实参考图的快照。初始计划不调用模型；AI完善计划和生成图片可能消耗接口额度。
                  </p>
                  <p>
                    原稿、旧封面和候选图继续保留，生成成功后需要审阅采用。真实操作截图请上传原图。
                  </p>
                  {!archived && (
                    <button
                      className="primary"
                      disabled={busy}
                      onClick={() =>
                        void run(async () => {
                          const editor = requireSaved();
                          const created = await identified<Plan>(base, {
                            post_version: editor.version,
                          });
                          client.setQueryData(
                            ["illustration-detail", id, created.id],
                            created,
                          );
                          setSelected(created.id);
                          setNotice("计划已保存，尚未调用模型或修改文章。");
                        })
                      }
                    >
                      建立配图计划
                    </button>
                  )}
                </>
              ) : detail.error ? (
                <p role="alert">{detail.error.message}</p>
              ) : !plan ? (
                <p>正在读取计划…</p>
              ) : (
                <PlanEditor
                  key={plan.id}
                  plan={plan}
                  busy={busy}
                  onDirty={setDirty}
                  run={run}
                  onChange={change}
                  onNotice={setNotice}
                  onPreview={(values) =>
                    request<ImagePreview>(
                      base + "/" + selected + "/preview",
                      values,
                    )
                  }
                  onAdopt={async (values) => {
                    requireSaved();
                    await onAdopt(plan.id, values);
                    setNotice(
                      "图片已采用并保存为文章新版本，原修订与旧图继续保留。",
                    );
                  }}
                />
              )}
            </section>
          </div>
        )}
        <footer>
          关闭此窗口保留输入，生成任务继续运行。完全退出会中断任务；图片失败不自动重复计费调用。
        </footer>
      </dialog>
    </>
  );
}

type EditorProps = {
  plan: Plan;
  busy: boolean;
  onDirty: (value: boolean) => void;
  run: (work: () => Promise<unknown>) => Promise<void>;
  onChange: (
    action: string,
    values: object,
    identified?: boolean,
  ) => Promise<Plan>;
  onPreview: (values: ImageAdoption) => Promise<ImagePreview>;
  onAdopt: (values: ImageAdoption) => Promise<void>;
  onNotice: (text: string) => void;
};
function PlanEditor({
  plan,
  busy,
  onDirty,
  run,
  onChange,
  onPreview,
  onAdopt,
  onNotice,
}: EditorProps) {
  const [pages, setPages] = useState(() => editablePages(plan.pages));
  const [basis, setBasis] = useState(() => ({
    version: plan.version,
    pages: editablePages(plan.pages),
  }));
  const [index, setIndex] = useState(0),
    [instruction, setInstruction] = useState("");
  const [parent, setParent] = useState("");
  const [chosen, setChosen] = useState("");
  const [placement, setPlacement] = useState(""),
    [caption, setCaption] = useState("");
  const [ack, setAck] = useState(false);
  const [preview, setPreview] = useState<{
    key: string;
    value: ImagePreview;
  } | null>(null);
  const [adoptionDirty, setAdoptionDirty] = useState(false);
  const planDirty = JSON.stringify(pages) !== JSON.stringify(basis.pages);
  const dirty = planDirty || !!instruction || adoptionDirty;
  const stale = basis.version !== plan.version;
  useEffect(() => {
    if (stale && !dirty) {
      const next = editablePages(plan.pages);
      setPages(next);
      setBasis({ version: plan.version, pages: next });
      setIndex(0);
      setChosen("");
      setPreview(null);
    }
  }, [stale, dirty, plan.version, plan.pages]);
  useEffect(() => {
    onDirty(dirty);
  }, [dirty, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);
  const locked =
    stale ||
    busy ||
    !!plan.archived_at ||
    ["queued", "running"].includes(plan.status);
  const page = pages[index],
    candidate = plan.candidates.find((c) => c.id === chosen);
  const changed =
    plan.source_changed || plan.profile_changed || !!candidate?.plan_changed;
  const values: ImageAdoption = {
    candidate_id: chosen,
    post_version: plan.post_version,
    placement,
    caption,
    ack_changes: ack,
  };
  const previewKey = JSON.stringify({ ...values, ack_changes: false });
  const validPreview = preview?.key === previewKey ? preview.value : null;
  function patch(values: Partial<ImagePage>) {
    setPages(pages.map((p, i) => (i === index ? { ...p, ...values } : p)));
  }
  function selectPage(next: number) {
    if (
      (instruction || adoptionDirty) &&
      !confirm("本页有未提交修改要求或图注，切换后放弃这些输入，是否继续？")
    )
      return;
    setIndex(next);
    setInstruction("");
    setParent("");
    setChosen("");
    setPreview(null);
    setAdoptionDirty(false);
    setAck(false);
  }
  function selectCandidate(id: string) {
    if (
      adoptionDirty &&
      !confirm("当前图注或位置尚未采用，切换后放弃这些输入，是否继续？")
    )
      return;
    const c = plan.candidates.find((item) => item.id === id)!;
    const prior = plan.adopted.find((a) => a.page_index === index) as
      | (Plan["adopted"][number] & { placement?: string; caption?: string })
      | undefined;
    setChosen(id);
    setCaption(
      prior?.candidate_id === id
        ? (prior.caption ?? "")
        : (c.page.caption ?? ""),
    );
    setPlacement(
      c.page.slot === "body"
        ? prior?.placement || c.page.placement || "legacy_end"
        : "",
    );
    setPreview(null);
    setAck(false);
    setAdoptionDirty(false);
  }
  async function upload(file: File) {
    if (file.size > 5000000) throw new Error("请选择5 MB以内的原图。");
    const data = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("读取原图失败"));
      reader.readAsDataURL(file);
    });
    const asset = await request<{ url: string }>("/illustrations/upload", {
      data,
      title: file.name.slice(0, 150),
    });
    await onChange(
      "import-original",
      { version: plan.version, page_index: index, image_url: asset.url },
      true,
    );
    onNotice("原图已作为本页候选保存，尚未写入文章。");
  }
  return (
    <>
      <div className="image-plan-heading">
        <h3>
          计划 v{plan.version} · {imageStatus[plan.status] ?? plan.status}
        </h3>
        <span>
          正文来源 v{plan.source_version} · 当前文章 v{plan.post_version}
        </span>
      </div>
      {stale && dirty && (
        <p className="warning">
          另一页面已更新配图计划，你的未保存输入仍保留。
          <button
            onClick={() => {
              if (!confirm("读取新版将放弃本地未保存的配图输入，是否继续？"))
                return;
              const next = editablePages(plan.pages);
              setPages(next);
              setBasis({ version: plan.version, pages: next });
              setInstruction("");
              setAdoptionDirty(false);
              setChosen("");
              setPreview(null);
              setIndex(0);
            }}
          >
            放弃本地输入并读取新版计划
          </button>
        </p>
      )}
      {plan.error && (
        <p role="alert" className="error">
          {plan.error} 原计划与旧图保留，可手动完善或保存计划后再生成。
        </p>
      )}
      {(plan.source_changed || plan.profile_changed) && (
        <p className="warning">
          文章或画像已变化。本计划仍使用创建时快照；可新建计划，采用旧候选需重新预览并明确核对。
        </p>
      )}
      <details className="image-style">
        <summary>
          画像风格与参考图 · {plan.profile.name || "未关联画像"}
        </summary>
        <p>
          {plan.profile.visual_style ||
            "此画像未填写独立视觉风格，可在画面描述中补充。"}
        </p>
        <div className="image-references">
          {plan.references.map((url) => (
            <a key={url} href={url} target="_blank" rel="noreferrer">
              <img src={url} alt="画像真实参考图" />
            </a>
          ))}
        </div>
        <p>
          下次规划：
          {plan.next_models.illustration_plan.requested_model || "未配置"}
          ；封面：{plan.next_models.cover.requested_model || "未配置"}；插图：
          {plan.next_models.illustration.requested_model || "未配置"}
          。可在侧边AI的“模型与生图设置”修改。
        </p>
      </details>
      <div className="image-actions">
        <button
          disabled={locked || !planDirty}
          onClick={() =>
            void run(async () => {
              const saved = await onChange("edit", {
                version: basis.version,
                pages,
              });
              const next = editablePages(saved.pages);
              setPages(next);
              setBasis({ version: saved.version, pages: next });
              onDirty(false);
              onNotice("配图计划已保存，文章保持原样。");
            })
          }
        >
          保存配图计划
        </button>
        <button
          disabled={locked || planDirty || !!plan.adopted.length}
          onClick={() => {
            if (
              confirm(
                "AI将基于已保存正文和画像完善配图计划，可能消耗文字模型额度，是否继续？",
              )
            )
              void run(() => onChange("improve", { version: plan.version }));
          }}
        >
          AI完善计划
        </button>
        <button
          disabled={
            busy || dirty || ["queued", "running"].includes(plan.status)
          }
          onClick={() =>
            void run(async () => {
              await onChange("archive", {
                version: plan.version,
                archived: !plan.archived_at,
              });
              onNotice(
                plan.archived_at
                  ? "计划已恢复。"
                  : "计划已归档，原图和文章保留。",
              );
            })
          }
        >
          {plan.archived_at ? "恢复配图计划" : "归档配图计划"}
        </button>
      </div>
      <div className="image-page-tabs" aria-label="配图页选择">
        {pages.map((p, i) => (
          <button
            key={i}
            aria-pressed={index === i}
            onClick={() => selectPage(i)}
            disabled={busy}
          >
            {i + 1}.{" "}
            {p.slot === "cover"
              ? "封面"
              : p.slot === "xhs"
                ? "图集"
                : "正文插图"}
          </button>
        ))}
        <button
          disabled={locked || pages.length >= 12 || !!plan.adopted.length}
          onClick={() => {
            setPages([
              ...pages,
              {
                title: "正文插图",
                text: "",
                prompt: "根据本节观点绘制概念说明插画，不伪造实测截图。",
                slot: "body",
                aspect: "landscape",
                placement: plan.placement_options[0]?.id || "",
                caption: "",
              },
            ]);
          }}
        >
          ＋ 添加插图
        </button>
      </div>
      {page && (
        <>
          <fieldset disabled={locked} className="image-page-fields">
            <legend>第 {index + 1} 页 · 画面与用途</legend>
            <div className="image-field-grid">
              <label>
                图标题
                <input
                  aria-label="图标题"
                  value={page.title}
                  maxLength={80}
                  onChange={(e) => patch({ title: e.target.value })}
                />
              </label>
              <label>
                图片用途
                <select
                  value={page.slot}
                  onChange={(e) =>
                    patch({
                      slot: e.target.value as ImagePage["slot"],
                      placement:
                        e.target.value === "body"
                          ? plan.placement_options[0]?.id || ""
                          : "",
                    })
                  }
                >
                  <option value="cover">文章封面</option>
                  <option value="body">正文插图</option>
                  <option value="xhs">小红书图集</option>
                </select>
              </label>
              <label>
                图片比例
                <select
                  value={page.aspect}
                  onChange={(e) =>
                    patch({ aspect: e.target.value as ImagePage["aspect"] })
                  }
                >
                  <option value="landscape">横图 3:2</option>
                  <option value="portrait">竖图 2:3</option>
                  <option value="square">方图 1:1</option>
                </select>
              </label>
            </div>
            <label>
              图中文字
              <textarea
                aria-label="图中文字"
                value={page.text}
                maxLength={500}
                onChange={(e) => patch({ text: e.target.value })}
              />
            </label>
            <label>
              画面描述
              <textarea
                aria-label="画面描述"
                rows={4}
                value={page.prompt}
                maxLength={5000}
                onChange={(e) => patch({ prompt: e.target.value })}
              />
            </label>
            {page.slot === "body" && (
              <Position
                plan={plan}
                value={page.placement ?? "legacy_end"}
                onChange={(placement) => patch({ placement })}
                label="计划插图位置"
              />
            )}
            <label>
              计划图片说明
              <input
                aria-label="计划图片说明"
                value={page.caption ?? ""}
                maxLength={300}
                onChange={(e) => patch({ caption: e.target.value })}
              />
            </label>
            <button
              disabled={pages.length === 1 || !!plan.adopted.length}
              onClick={() => {
                setPages(pages.filter((_, i) => i !== index));
                setIndex(0);
                setChosen("");
              }}
            >
              移除此页
            </button>
          </fieldset>
          <fieldset disabled={locked || planDirty} className="image-generation">
            <legend>生成或上传本页候选</legend>
            <p>
              每次仅处理这一张；失败后先核对服务计费再手动生成，原图和已采用图片继续保留。
            </p>
            <label>
              修改基于
              <select
                aria-label="修改基于"
                value={parent}
                onChange={(e) => setParent(e.target.value)}
              >
                <option value="">新生成候选图</option>
                {plan.candidates
                  .filter(
                    (c) =>
                      c.page_index === index &&
                      c.status === "review" &&
                      c.origin !== "original",
                  )
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      候选 {c.id.slice(0, 8)}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              本次修改要求
              <textarea
                aria-label="本次修改要求"
                value={instruction}
                maxLength={2000}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder="例如：保留构图，把右侧装饰简化。"
              />
            </label>
            <div className="image-actions">
              <button
                className="primary"
                disabled={plan.status !== "review"}
                onClick={() => {
                  if (
                    confirm(
                      "本次将调用图片模型生成一张候选图，可能产生费用，是否继续？",
                    )
                  )
                    void run(async () => {
                      await onChange(
                        "generate",
                        {
                          version: plan.version,
                          page_index: index,
                          instruction,
                          parent_id: parent || null,
                        },
                        true,
                      );
                      setInstruction("");
                      onNotice("候选图任务已保存，可稍后回来查看。");
                    });
                }}
              >
                {parent ? "基于候选图修改" : "生成本页候选图"}
              </button>
              <label className="image-upload">
                上传原图
                <input
                  aria-label="上传本页原图"
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    e.target.value = "";
                    if (file) void run(() => upload(file));
                  }}
                />
              </label>
            </div>
            {planDirty && <p>请先保存配图计划。</p>}
          </fieldset>
          <div className="image-candidates" aria-label="本页候选图片">
            {plan.candidates
              .filter((c) => c.page_index === index)
              .map((c) => (
                <article
                  key={c.id}
                  className={chosen === c.id ? "selected" : ""}
                >
                  {c.image_url && (
                    <a href={c.image_url} target="_blank" rel="noreferrer">
                      <img src={c.image_url} alt={c.page.title + "候选图片"} />
                    </a>
                  )}
                  <p>
                    <strong>
                      {c.origin === "original" ? "上传原图" : "AI候选图"}
                    </strong>{" "}
                    · {imageStatus[c.status] ?? c.status} · {c.id.slice(0, 8)}
                  </p>
                  <small>
                    {c.origin === "original"
                      ? "原图未重绘"
                      : `请求模型：${c.model_provenance.requested_model || "历史未记录"}`}
                    {c.model_provenance.reported_model
                      ? `；接口报告：${c.model_provenance.reported_model}`
                      : ""}
                  </small>
                  {c.error && (
                    <p role="alert" className="error">
                      {c.error}
                    </p>
                  )}
                  {c.plan_changed && <p className="warning">基于旧版计划</p>}
                  {plan.adopted.some((a) => a.candidate_id === c.id) && (
                    <p className="notice">已采用</p>
                  )}
                  {c.image_url && (
                    <div className="image-actions">
                      <a href={c.image_url} download>
                        下载原图
                      </a>
                      <button
                        disabled={locked || planDirty}
                        onClick={() => selectCandidate(c.id)}
                      >
                        选择并预览
                      </button>
                    </div>
                  )}
                </article>
              ))}
          </div>
          {candidate && (
            <fieldset disabled={locked || planDirty} className="image-adoption">
              <legend>采用前核对 · {candidate.id.slice(0, 8)}</legend>
              {candidate.page.slot === "body" && (
                <Position
                  plan={plan}
                  value={placement}
                  onChange={(value) => {
                    setPlacement(value);
                    setAdoptionDirty(true);
                  }}
                  label="采用插图位置"
                />
              )}
              {candidate.page.slot === "body" && (
                <label>
                  采用图片说明
                  <input
                    aria-label="采用图片说明"
                    value={caption}
                    maxLength={300}
                    onChange={(e) => {
                      setCaption(e.target.value);
                      setAdoptionDirty(true);
                    }}
                  />
                </label>
              )}
              <button
                onClick={() =>
                  void run(async () => {
                    const result = await onPreview(values);
                    setPreview({ key: previewKey, value: result });
                  })
                }
              >
                预览采用位置
              </button>
              {validPreview && (
                <div className="image-adoption-preview">
                  <p>
                    预览当前文章 v{validPreview.post_version} ·{" "}
                    {validPreview.placement_label}
                  </p>
                  {validPreview.cover && (
                    <img
                      className="image-cover-preview"
                      src={validPreview.cover}
                      alt="采用后的文章封面预览"
                    />
                  )}
                  <article
                    className="prose"
                    dangerouslySetInnerHTML={{
                      __html: validPreview.preview_html,
                    }}
                  />
                </div>
              )}
              {changed && (
                <label className="image-ack">
                  <input
                    type="checkbox"
                    checked={ack}
                    onChange={(e) => setAck(e.target.checked)}
                  />
                  我已核对文章、画像或计划的变化，仍采用这张旧快照图片
                </label>
              )}
              <button
                className="primary"
                disabled={!validPreview || (changed && !ack)}
                onClick={() =>
                  void run(async () => {
                    await onAdopt(values);
                    setAdoptionDirty(false);
                    setPreview(null);
                  })
                }
              >
                确认采用图片并保存文章
              </button>
            </fieldset>
          )}
        </>
      )}
    </>
  );
}
function Position({
  plan,
  value,
  onChange,
  label,
}: {
  plan: Plan;
  value: string;
  onChange: (value: string) => void;
  label: string;
}) {
  return (
    <label>
      {label}
      <select
        aria-label={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">请选择章节或段落</option>
        {value === "legacy_end" && (
          <option value="legacy_end">旧计划：追加到文章末尾</option>
        )}
        {value &&
          value !== "legacy_end" &&
          !plan.placement_options.some((p) => p.id === value) && (
            <option value={value}>原位置已变化，请重新选择</option>
          )}
        {plan.placement_options.map((p) => (
          <option key={p.id} value={p.id}>
            {p.label}
          </option>
        ))}
      </select>
    </label>
  );
}
