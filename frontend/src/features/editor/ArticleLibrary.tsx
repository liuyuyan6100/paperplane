import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { loadArticle, request, type ArticleRow } from "../../api";

type Scope = "active" | "archived" | "trash";
type Action = "archive" | "unarchive" | "trash" | "restore";
export function ArticleLibrary({
  rows,
  selected,
  error,
  onOpen,
  onSample,
  beforeChange,
  onRemoved,
  onBusy,
}: {
  rows: ArticleRow[];
  selected: string | null;
  error?: string;
  onOpen: (id: string) => void;
  onSample: () => void;
  beforeChange: (row: ArticleRow, action: Action) => number | null;
  onRemoved: (id: string) => void;
  onBusy: (busy: boolean) => void;
}) {
  const [scope, setScope] = useState<Scope>("active");
  const [search, setSearch] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [failure, setFailure] = useState("");
  const [reading, setReading] = useState<{
    title: string;
    html: string;
  } | null>(null);
  const preview = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const other = useQuery({
    queryKey: ["articles", scope],
    queryFn: () =>
      request<{ articles: ArticleRow[] }>("/articles?scope=" + scope),
    enabled: scope !== "active",
    refetchInterval: scope !== "active" ? 5000 : false,
  });
  useEffect(() => {
    if (reading) preview.current?.showModal();
    else preview.current?.close();
  }, [reading]);
  const source = scope === "active" ? rows : (other.data?.articles ?? []);
  const list = source.filter((row) =>
    (row.title + row.summary).toLowerCase().includes(search.toLowerCase()),
  );
  async function inspect(row: ArticleRow) {
    setFailure("");
    try {
      const article = await loadArticle(row.id);
      const html = await request<{ html: string }>("/preview", {
        body: article.body,
      });
      setReading({ title: article.title, html: html.html });
    } catch (err) {
      setFailure((err as Error).message);
    }
  }
  async function change(row: ArticleRow, action: Action) {
    if (busy) return;
    setFailure("");
    setNotice("");
    try {
      const version = beforeChange(row, action);
      if (version === null) return;
      setBusy(true);
      onBusy(true);
      await request("/articles/" + row.id + "/manage", { action, version });
      if (action === "archive" || action === "trash") onRemoved(row.id);
      setMenu(null);
      setNotice(
        action === "trash"
          ? "已移入回收站，可在回收站恢复。"
          : action === "archive"
            ? "已归档，可在归档中取消归档。"
            : "文章已恢复，原内容和历史保留。",
      );
      await client.invalidateQueries({ queryKey: ["articles"] });
      await client.invalidateQueries({ queryKey: ["creation-materials"] });
    } catch (err) {
      setFailure((err as Error).message);
    } finally {
      setBusy(false);
      onBusy(false);
    }
  }
  return (
    <>
      <label className="search">
        <span>搜索文章</span>
        <input
          placeholder="标题或摘要"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </label>
      <nav className="library-scopes" aria-label="文章范围">
        {(
          [
            ["active", "文章"],
            ["archived", "归档"],
            ["trash", "回收站"],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            aria-pressed={scope === value}
            disabled={busy}
            onClick={() => {
              setScope(value);
              setMenu(null);
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="list-heading">
        {scope === "active"
          ? "验证库文章"
          : scope === "archived"
            ? "已归档"
            : "回收站"}{" "}
        <span>{source.length}</span>
      </div>
      {notice && (
        <p className="library-feedback" role="status">
          {notice}
        </p>
      )}
      {(failure || other.error || error) && (
        <p className="library-feedback" role="alert">
          {failure || other.error?.message || error}
        </p>
      )}
      <div className="article-list">
        {list.map((row) => (
          <div
            className="article-row"
            key={row.id}
            onContextMenu={(e) => {
              e.preventDefault();
              setMenu(row.id);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") setMenu(null);
            }}
          >
            <button
              className={
                "article-item" + (selected === row.id ? " selected" : "")
              }
              disabled={busy}
              onClick={() => {
                setMenu(null);
                if (scope === "active") onOpen(row.id);
                else void inspect(row);
              }}
            >
              <strong>{row.title}</strong>
              <span>{row.summary || "暂无摘要"}</span>
              <small>
                {
                  {
                    idea: "选题中",
                    draft: "写作中",
                    ready: "待发布",
                    published: "已发布",
                  }[row.stage]
                }{" "}
                · 第 {row.version} 版
              </small>
            </button>
            <button
              className="article-more"
              aria-label={row.title + "的操作"}
              aria-expanded={menu === row.id}
              disabled={busy}
              onClick={() => setMenu(menu === row.id ? null : row.id)}
            >
              ⋯
            </button>
            {menu === row.id && (
              <div
                className="article-actions"
                role="group"
                aria-label={row.title + "操作"}
              >
                {scope !== "trash" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void change(
                        row,
                        scope === "active" ? "archive" : "unarchive",
                      )
                    }
                  >
                    {scope === "active" ? "归档" : "取消归档"}
                  </button>
                )}
                <button
                  disabled={busy}
                  onClick={() =>
                    void change(row, scope === "trash" ? "restore" : "trash")
                  }
                >
                  {scope === "trash" ? "恢复文章" : "删除（移入回收站）"}
                </button>
              </div>
            )}
          </div>
        ))}
        {!list.length && (
          <div className="empty-list">
            <p>
              {search
                ? "没有符合搜索条件的文章。"
                : scope === "active"
                  ? "还没有文章，从一篇测试稿开始。"
                  : scope === "trash"
                    ? "回收站为空。"
                    : "没有归档文章。"}
            </p>
            {scope === "active" && (
              <button onClick={onSample}>创建验收样稿</button>
            )}
          </div>
        )}
      </div>
      <dialog
        ref={preview}
        className="library-preview"
        aria-label="文章只读查看"
        onCancel={(e) => {
          e.preventDefault();
          setReading(null);
        }}
      >
        <header>
          <h2>{reading?.title}</h2>
          <button onClick={() => setReading(null)}>关闭查看</button>
        </header>
        <p>
          当前文章为{scope === "trash" ? "回收站" : "归档"}
          内容。恢复后可继续编辑，原修订与任务历史保留。
        </p>
        <article
          className="prose"
          dangerouslySetInnerHTML={{ __html: reading?.html ?? "" }}
        />
      </dialog>
    </>
  );
}
