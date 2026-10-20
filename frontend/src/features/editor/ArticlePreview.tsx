import { useEffect, useRef, useState } from "react";
import { request } from "../../api";
import type { Draft } from "./session";

export function ArticlePreview({
  open,
  draft,
  onClose,
  onImages,
}: {
  open: boolean;
  draft: Draft;
  onClose: () => void;
  onImages: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [mobile, setMobile] = useState(false);
  const [html, setHtml] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setHtml("");
    setError("");
    request<{ html: string }>(
      "/preview",
      { body: draft.body },
      controller.signal,
    )
      .then((r) => {
        if (!controller.signal.aborted) setHtml(r.html);
      })
      .catch((e: Error) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [open, draft.body]);
  return (
    <dialog
      ref={dialog}
      className="article-preview"
      aria-label="图文成品预览"
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2>图文成品预览</h2>
          <p>查看封面与正文的阅读效果，尚未公开发表。</p>
        </div>
        <div className="image-actions">
          <button aria-pressed={!mobile} onClick={() => setMobile(false)}>
            桌面阅读
          </button>
          <button aria-pressed={mobile} onClick={() => setMobile(true)}>
            手机阅读
          </button>
          <button onClick={onImages}>继续配图</button>
          <button className="primary" onClick={onClose}>
            返回编辑
          </button>
        </div>
      </header>
      <div className="article-preview-viewport">
        <article
          className={"article-preview-page" + (mobile ? " is-mobile" : "")}
        >
          {draft.cover ? (
            <img
              className="article-preview-cover"
              src={draft.cover}
              alt="文章封面"
            />
          ) : (
            <div className="article-preview-empty">
              尚未设置文章封面<button onClick={onImages}>准备封面与插图</button>
            </div>
          )}
          <div className="article-preview-text">
            <h1>{draft.title || "未命名文章"}</h1>
            {draft.author && (
              <p className="article-preview-author">{draft.author}</p>
            )}
            {draft.summary && (
              <p className="article-preview-summary">{draft.summary}</p>
            )}
            {error ? (
              <p role="alert">{error}</p>
            ) : html ? (
              <div
                className="prose"
                dangerouslySetInnerHTML={{ __html: html }}
              />
            ) : (
              <p role="status">正在排版预览…</p>
            )}
          </div>
        </article>
      </div>
    </dialog>
  );
}
