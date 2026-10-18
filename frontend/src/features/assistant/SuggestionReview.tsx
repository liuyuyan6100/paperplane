import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { request } from "../../api";
import { changedBlocks } from "./blockDiff";

export type Content = { title: string; body: string };

function highlight(before: string, after: string) {
  const parse = (html: string) =>
    new DOMParser().parseFromString(html, "text/html");
  const docs = [parse(before), parse(after)];
  const nodes = docs.map((doc) => Array.from(doc.body.children));
  const diff = changedBlocks(
    nodes[0].map((node) => node.outerHTML),
    nodes[1].map((node) => node.outerHTML),
  );
  [diff.left, diff.right].forEach((changes, side) => {
    changes.forEach((index) =>
      nodes[side][index].setAttribute(
        "data-review-change",
        side ? "added" : "removed",
      ),
    );
  });
  return {
    before: docs[0].body.innerHTML,
    after: docs[1].body.innerHTML,
    diff,
  };
}

export function SuggestionReview({
  before,
  after,
  stale,
  busy,
  error,
  onClose,
  onContinue,
  onAdopt,
}: {
  before?: Content;
  after: Content;
  stale: boolean;
  busy: boolean;
  error: string;
  onClose: () => void;
  onContinue: () => void;
  onAdopt: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [marked, setMarked] = useState(true);
  const preview = useQuery({
    queryKey: ["suggestion-reading", before?.body, after.body],
    queryFn: async ({ signal }) => {
      const [original, suggestion] = await Promise.all([
        request<{ html: string }>(
          "/preview",
          { body: before?.body ?? "" },
          signal,
        ),
        request<{ html: string }>("/preview", { body: after.body }, signal),
      ]);
      return highlight(original.html, suggestion.html);
    },
    staleTime: Infinity,
    retry: false,
  });
  useEffect(() => {
    const element = dialog.current;
    const previous = document.activeElement;
    element?.showModal();
    return () => {
      element?.close();
      if (
        previous instanceof HTMLElement &&
        previous.isConnected &&
        (document.activeElement === document.body ||
          element?.contains(document.activeElement))
      )
        previous.focus();
    };
  }, []);
  return (
    <dialog
      ref={dialog}
      className={"suggestion-review" + (marked ? " show-changes" : "")}
      aria-labelledby="suggestion-review-title"
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(
          event.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]',
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
      <header className="review-header">
        <div>
          <span className="milestone">AI 修改建议</span>
          <h2 id="suggestion-review-title">审阅修改对比</h2>
          <p>
            左侧是发送本次请求时的原稿，右侧是完整建议稿。填入后仍需自行保存。
          </p>
        </div>
        <button onClick={onClose} autoFocus>
          关闭对比
        </button>
      </header>
      <div className="review-options">
        <label>
          <input
            type="checkbox"
            checked={marked}
            onChange={(event) => setMarked(event.target.checked)}
          />
          突出改动
        </label>
        <span>
          <i className="removed-key" />
          删改前 <i className="added-key" />
          新增或改写
        </span>
        {preview.data && (
          <small>
            正文标出 {preview.data.diff.left.size} 个原稿块 /{" "}
            {preview.data.diff.right.size} 个建议块
          </small>
        )}
      </div>
      {stale && (
        <p className="review-warning">
          当前文章已变化，此处保留当时的快照供阅读，不能直接填入旧建议。
        </p>
      )}
      {!before && (
        <p className="review-warning">
          此条历史建议没有原稿快照，仅供阅读建议稿。
        </p>
      )}
      {preview.data?.diff.approximate && (
        <p className="review-warning">
          篇幅较长，中间变动区域整体标色；两份全文均完整保留。
        </p>
      )}
      <div className="review-content">
        {preview.isPending ? (
          <p role="status">正在排版对比…</p>
        ) : preview.error ? (
          <div role="alert">
            <p>对比排版失败：{preview.error.message}</p>
            <button onClick={() => void preview.refetch()}>重新加载对比</button>
          </div>
        ) : (
          <div className="review-columns">
            <section aria-label="修改前">
              <h3>
                修改前 <small>本次请求时的原稿</small>
              </h3>
              <h2
                className={
                  before?.title !== after.title ? "review-title-removed" : ""
                }
              >
                {before?.title || "无原稿快照"}
              </h2>
              <article
                className="prose"
                dangerouslySetInnerHTML={{ __html: preview.data.before }}
              />
            </section>
            <section aria-label="建议稿">
              <h3>
                建议稿 <small>待你审阅采用</small>
              </h3>
              <h2
                className={
                  before?.title !== after.title ? "review-title-added" : ""
                }
              >
                {after.title}
              </h2>
              <article
                className="prose"
                dangerouslySetInnerHTML={{ __html: preview.data.after }}
              />
            </section>
          </div>
        )}
      </div>
      <footer className="review-footer">
        <div>
          {error ? (
            <p role="alert">{error}</p>
          ) : (
            <span>查看和关闭对比不会改动文章。</span>
          )}
        </div>
        {stale ? (
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              dialog.current?.close();
              onClose();
              onContinue();
            }}
          >
            基于当前稿继续修改
          </button>
        ) : (
          <button
            className="primary"
            disabled={busy || !before || !preview.data}
            onClick={onAdopt}
          >
            {busy ? "正在填入…" : "填入编辑区"}
          </button>
        )}
      </footer>
    </dialog>
  );
}
