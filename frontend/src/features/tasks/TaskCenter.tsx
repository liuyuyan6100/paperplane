import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";

interface Task {
  kind: string;
  id: string;
  title: string;
  post_id: string;
  owner_id: string;
  kind_label: string;
  status: string;
  status_label: string;
  group: string;
  stage: string;
  stage_label: string;
  description: string;
  error: string;
  error_kind: string;
  created_at: string;
  updated_at: string;
  attempts: number | null;
  revision: string;
  actions: string[];
  legacy_url: string;
  instruction: string;
}
interface Detail extends Task {
  events: { at: string; status: string; message: string }[];
  receipt: { external_id: string; url: string } | null;
}
interface Page {
  tasks: Task[];
  counts: Record<string, number>;
  total: number;
  offset: number;
  limit: number;
  kinds: { id: string; label: string }[];
  workers: Record<string, { alive: boolean; error: string }>;
  checked_at: string;
}
const groups = {
  all: "全部",
  active: "执行中",
  attention: "需处理",
  review: "待确认",
  done: "已结束",
};
const errors: Record<string, string> = {
  unknown_delivery: "原平台是否收到尚未确认，不能直接重发。",
  configuration: "请先检查已有配置或账号权限，再手动重试。",
  temporary: "可能是临时网络或服务问题；此处不会自动重复执行。",
  interrupted: "执行已中断，原输入与已完成记录保留。",
  result_format: "结果未通过格式检查，原稿保留。",
  needs_review: "请核对错误和原工作项后再决定如何继续。",
};
const actions: Record<string, string> = {
  retry: "重试原任务",
  cancel: "取消本任务",
  recheck: "只读核对原草稿",
};
function when(value: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

export function TasksButton({
  articleId,
  onOpenArticle,
}: {
  articleId: string | null;
  onOpenArticle: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [articleOnly, setArticleOnly] = useState(false);
  const summary = useQuery({
    queryKey: ["tasks", "summary"],
    queryFn: ({ signal }) => request<Page>("/tasks?limit=1", undefined, signal),
    refetchInterval: 5000,
  });
  const count = summary.data?.counts.attention ?? 0;
  const current = useQuery({
    queryKey: ["tasks", "article-summary", articleId],
    queryFn: ({ signal }) =>
      request<Page>(`/tasks?post_id=${articleId}&limit=1`, undefined, signal),
    enabled: !!articleId,
    refetchInterval: 3000,
  });
  const counts = current.data?.counts;
  const label = counts?.attention
    ? `本篇：${counts.attention} 项需处理`
    : counts?.active
      ? `本篇：${counts.active} 项处理中`
      : counts?.review
        ? `本篇：${counts.review} 项待确认`
        : "";
  return (
    <>
      {label && (
        <button
          className="article-tasks"
          onClick={() => {
            setArticleOnly(true);
            setOpen(true);
          }}
        >
          {label}
        </button>
      )}
      <button
        aria-label="任务中心"
        title="查看 AI 处理进度、待确认结果与失败记录"
        onClick={() => {
          setArticleOnly(false);
          setOpen(true);
        }}
      >
        任务中心{" "}
        {count > 0 && <span className="task-count">{count} 需处理</span>}
      </button>
      {open && (
        <TaskCenter
          articleId={articleId}
          initialCurrentOnly={articleOnly}
          onClose={() => setOpen(false)}
          onOpenArticle={(id) => {
            setOpen(false);
            onOpenArticle(id);
          }}
        />
      )}
    </>
  );
}

function VerifiedMissing({
  busy,
  onConfirm,
}: {
  busy: boolean;
  onConfirm: () => void;
}) {
  const [verified, setVerified] = useState(false);
  return (
    <fieldset className="task-verification">
      <legend>人工核实未收到</legend>
      <p>先使用上方只读核对；无法自动确认时，到目标平台检查原文章和草稿箱。</p>
      <label>
        <input
          type="checkbox"
          checked={verified}
          onChange={(e) => setVerified(e.target.checked)}
        />
        我已到原平台核实，这篇文章确实未收到
      </label>
      <button disabled={!verified || busy} onClick={onConfirm}>
        标记为未收到
      </button>
      <small>
        此步只记录核实结果，不会发送。之后仍须主动点击“重试原任务”。
      </small>
    </fieldset>
  );
}

export function TaskCenter({
  articleId,
  initialCurrentOnly = false,
  onClose,
  onOpenArticle,
}: {
  articleId: string | null;
  initialCurrentOnly?: boolean;
  onClose: () => void;
  onOpenArticle: (id: string) => void;
}) {
  const root = useRef<HTMLDialogElement>(null);
  const [group, setGroup] = useState("all");
  const [kind, setKind] = useState("");
  const [query, setQuery] = useState("");
  const [currentOnly, setCurrentOnly] = useState(initialCurrentOnly);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<{ kind: string; id: string } | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const active = useRef(false);
  const pending = useRef<{ signature: string; requestId: string } | null>(null);
  const client = useQueryClient();
  const filters = {
    group,
    kind,
    query,
    offset,
    post_id: currentOnly ? articleId : null,
  };
  const listing = useQuery({
    queryKey: ["tasks", filters],
    queryFn: ({ signal }) => {
      const params = new URLSearchParams({
        group,
        query,
        offset: String(offset),
        limit: "30",
      });
      if (kind) params.set("kind", kind);
      if (currentOnly && articleId) params.set("post_id", articleId);
      return request<Page>("/tasks?" + params, undefined, signal);
    },
    refetchInterval: 2000,
  });
  const detail = useQuery({
    queryKey: ["task-detail", selected?.kind, selected?.id],
    queryFn: ({ signal }) =>
      request<Detail>(
        `/tasks/${selected!.kind}/${selected!.id}`,
        undefined,
        signal,
      ),
    enabled: !!selected,
    refetchInterval: 2000,
  });
  useEffect(() => {
    const element = root.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  async function command(task: Detail, action: string, verified = false) {
    if (active.current) return;
    active.current = true;
    setBusy(true);
    setFailure("");
    setNotice("");
    const signature = JSON.stringify([
      task.kind,
      task.id,
      action,
      task.revision,
      verified,
    ]);
    if (pending.current?.signature !== signature)
      pending.current = {
        signature,
        requestId: crypto.randomUUID().replaceAll("-", ""),
      };
    try {
      await request(`/tasks/${task.kind}/${task.id}/commands`, {
        request_id: pending.current.requestId,
        revision: task.revision,
        action,
        verified,
      });
      pending.current = null;
      setNotice(
        action === "not_received"
          ? "已记录人工核实结果，尚未重发。"
          : "操作已提交，请查看任务的最新状态。",
      );
    } catch (error) {
      const err = error as Error & {
        status?: number;
        details?: { command_status?: string };
      };
      setFailure(err.message);
      if (err.status && err.status < 500 && !err.details?.command_status)
        pending.current = null;
    } finally {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["tasks"] }),
        client.invalidateQueries({
          queryKey: ["task-detail", task.kind, task.id],
        }),
      ]);
      active.current = false;
      setBusy(false);
    }
  }
  const task = detail.data;
  function scope(update: () => void) {
    setOffset(0);
    update();
  }
  return (
    <dialog
      ref={root}
      className="task-center"
      aria-labelledby="task-center-heading"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), [tabindex="0"]',
          ),
        ).filter((node) => node.getClientRects().length);
        const first = controls[0],
          last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
    >
      <header className="task-center-header">
        <div>
          <span className="milestone">M3 · 本机任务</span>
          <h2 id="task-center-heading">任务中心</h2>
          <p>AI 有没有做完、哪里需要你确认、失败后怎么继续，都在这里查看。</p>
          <p className="task-purpose">
            写文章不必一直打开。发送 AI
            请求后会自动留下记录；“待确认”是等你审阅建议，“需处理”是失败、中断或结果待核对。这里不会自动采用或发布文章。
          </p>
        </div>
        <button autoFocus onClick={onClose}>
          关闭任务中心
        </button>
      </header>
      <div className="task-center-filters">
        <div className="task-groups" aria-label="按任务状态筛选">
          {Object.entries(groups).map(([value, label]) => (
            <button
              key={value}
              aria-pressed={group === value}
              onClick={() => scope(() => setGroup(value))}
            >
              {label} <span>{listing.data?.counts[value] ?? "—"}</span>
            </button>
          ))}
        </div>
        <div className="task-search">
          <label>
            搜索任务
            <input
              aria-label="搜索任务"
              value={query}
              placeholder="标题、要求或任务编号"
              maxLength={200}
              onChange={(e) => scope(() => setQuery(e.target.value))}
            />
          </label>
          <label>
            任务类型
            <select
              aria-label="任务类型"
              value={kind}
              onChange={(e) => scope(() => setKind(e.target.value))}
            >
              <option value="">全部类型</option>
              {listing.data?.kinds.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="task-current">
            <input
              type="checkbox"
              checked={currentOnly}
              disabled={!articleId}
              onChange={(e) => scope(() => setCurrentOnly(e.target.checked))}
            />
            仅当前文章
          </label>
          <button
            onClick={() => {
              void listing.refetch();
              if (selected) void detail.refetch();
            }}
          >
            刷新状态
          </button>
        </div>
        {listing.data && (
          <small className="task-worker-state">
            送稿调度：
            {listing.data.workers.delivery.alive ? "运行中" : "未运行"} ·
            创作调度：
            {listing.data.workers.creation.alive ? "运行中" : "未运行"} ·
            最近读取 {when(listing.data.checked_at)}
          </small>
        )}
        {Object.entries(listing.data?.workers ?? {}).map(([name, worker]) =>
          worker.error ? (
            <p key={name} role="alert">
              {worker.error}
            </p>
          ) : null,
        )}
      </div>
      {(failure || notice) && (
        <p
          className={failure ? "task-command-error" : "task-command-notice"}
          role={failure ? "alert" : "status"}
        >
          {failure || notice}
        </p>
      )}
      <div className="task-center-content">
        <nav className="task-list" aria-label="任务列表">
          {listing.isPending ? (
            <p role="status">正在读取任务…</p>
          ) : listing.error ? (
            <p role="alert">{listing.error.message}</p>
          ) : !listing.data.tasks.length ? (
            <p className="task-empty">
              此范围没有任务。可回到文章发送一条 AI 讨论，再查看执行过程。
            </p>
          ) : (
            listing.data.tasks.map((item) => (
              <button
                key={item.kind + item.id}
                className={
                  selected?.kind === item.kind && selected.id === item.id
                    ? "task-row selected"
                    : "task-row"
                }
                onClick={() => {
                  setSelected({ kind: item.kind, id: item.id });
                  setFailure("");
                  setNotice("");
                }}
              >
                <span className="task-row-meta">
                  {item.kind_label}
                  <span className={"task-state " + item.group}>
                    {item.status_label}
                  </span>
                </span>
                <strong>{item.title}</strong>
                <span className="task-excerpt">
                  {item.error || item.description || item.instruction}
                </span>
                <small>{when(item.updated_at)}</small>
              </button>
            ))
          )}
          <div className="task-pagination">
            <button
              disabled={offset === 0 || listing.isFetching}
              onClick={() => setOffset(Math.max(0, offset - 30))}
            >
              上一页
            </button>
            <small>
              {listing.data?.total ? offset + 1 : 0}–
              {offset + (listing.data?.tasks.length ?? 0)} /{" "}
              {listing.data?.total ?? 0}
            </small>
            <button
              disabled={
                !listing.data ||
                offset + 30 >= listing.data.total ||
                listing.isFetching
              }
              onClick={() => setOffset(offset + 30)}
            >
              下一页
            </button>
          </div>
        </nav>
        <section className="task-detail" aria-label="任务详情">
          {!selected ? (
            <div className="task-empty">
              <h3>选择一条任务查看详情</h3>
              <p>保留原编号、执行记录和确认点。查看不会发起生成或送稿。</p>
            </div>
          ) : detail.isPending ? (
            <p role="status">正在读取详情…</p>
          ) : detail.error ? (
            <p role="alert">{detail.error.message}</p>
          ) : (
            task && (
              <>
                <div className="task-detail-heading">
                  <span>{task.kind_label}</span>
                  <span className={"task-state " + task.group}>
                    {task.status_label}
                  </span>
                </div>
                <h3>{task.title}</h3>
                <code className="task-id">{task.id}</code>
                <dl className="task-facts">
                  <div>
                    <dt>当前阶段</dt>
                    <dd>{task.stage_label || task.status_label}</dd>
                  </div>
                  <div>
                    <dt>已开始执行</dt>
                    <dd>
                      {task.attempts !== null
                        ? task.attempts + " 次"
                        : "此类任务未单独记录次数"}
                    </dd>
                  </div>
                  <div>
                    <dt>{task.kind === "visual" ? "所属改编建立" : "建立"}</dt>
                    <dd>{when(task.created_at)}</dd>
                  </div>
                  <div>
                    <dt>{task.kind === "visual" ? "所属改编更新" : "更新"}</dt>
                    <dd>{when(task.updated_at)}</dd>
                  </div>
                </dl>
                <p className="task-next-step">{task.instruction}</p>
                {task.error && (
                  <div className="task-error-evidence">
                    <strong>{errors[task.error_kind] || "请核对原任务"}</strong>
                    <p>{task.error}</p>
                    {task.error_kind === "configuration" && (
                      <a
                        href="/#settings"
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        查看原工作台配置 ↗
                      </a>
                    )}
                  </div>
                )}
                {task.description && (
                  <section className="task-description">
                    <h4>原要求或处理说明</h4>
                    <p>{task.description}</p>
                  </section>
                )}
                {task.receipt?.external_id && (
                  <section className="task-receipt">
                    <h4>已有平台回执</h4>
                    <p>编号：{task.receipt.external_id}</p>
                    {task.receipt.url && (
                      <a
                        href={task.receipt.url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        打开已有平台地址 ↗
                      </a>
                    )}
                    <small>平台草稿回执不代表已公开发表。</small>
                  </section>
                )}
                <div className="task-detail-actions">
                  {task.post_id && (
                    <button onClick={() => onOpenArticle(task.post_id)}>
                      打开所属文章
                    </button>
                  )}
                  {task.legacy_url && (
                    <a
                      className="task-owner-link"
                      href={task.legacy_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      到原工作台处理 ↗
                    </a>
                  )}
                  {task.actions
                    .filter((action) => action !== "not_received")
                    .map((action) => (
                      <button
                        key={action}
                        disabled={busy}
                        className={action === "retry" ? "primary" : ""}
                        onClick={() => void command(task, action)}
                      >
                        {busy ? "处理中…" : actions[action]}
                      </button>
                    ))}
                </div>
                {task.kind === "workflow" &&
                  task.actions.includes("cancel") && (
                    <p className="task-action-hint">
                      取消后不采用返回内容；已经发出的模型请求可能继续到结束。
                    </p>
                  )}
                {task.actions.includes("not_received") && (
                  <VerifiedMissing
                    key={task.revision}
                    busy={busy}
                    onConfirm={() => void command(task, "not_received", true)}
                  />
                )}
                <section className="task-events">
                  <h4>
                    处理记录 <small>最近 100 条</small>
                  </h4>
                  {task.events.length ? (
                    <ol>
                      {task.events.map((event, index) => (
                        <li key={index}>
                          <small>{when(event.at)}</small>
                          <p>{event.message}</p>
                        </li>
                      ))}
                    </ol>
                  ) : (
                    <p>
                      此类任务未保存独立阶段日志；当前状态、原要求和原工作项记录仍保留。
                    </p>
                  )}
                </section>
              </>
            )
          )}
        </section>
      </div>
      <footer className="task-center-footer">
        关闭任务中心不会停止任务。完全退出本机程序会中断正在执行的本机任务，重开后按原记录处理。
      </footer>
    </dialog>
  );
}
