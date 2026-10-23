import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { COMPLETED, PLANNED, PRODUCT_AUTHOR, ROADMAP_UPDATED } from "./roadmap";

export function RoadmapPanel({ openRequest = 0 }: { openRequest?: number }) {
  const [open, setOpen] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (openRequest) setOpen(true); }, [openRequest]);
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  return <>
    <button className="roadmap-entry" onClick={() => setOpen(true)}>功能进度与规划</button>
    {createPortal(<dialog ref={dialog} className="roadmap-panel" aria-label="功能进度与规划"
      onCancel={(event) => { event.preventDefault(); setOpen(false); }}>
      <header className="roadmap-header">
        <div><span className="milestone">纸飞机 · 作者：{PRODUCT_AUTHOR}</span>
          <h2>功能进度与规划</h2><p>更新于 {ROADMAP_UPDATED} · 随应用版本更新</p></div>
        <button onClick={() => setOpen(false)}>返回编辑器</button>
      </header>
      <div className="roadmap-body">
        <div className="roadmap-summary" aria-label="交付概览">
          <span><strong>创作主线</strong>已打通</span><span><strong>当前交付</strong>图文检查与草稿待验收</span><span><strong>后续计划</strong>单平台改编</span>
        </div>
        <section aria-labelledby="roadmap-completed"><h3 id="roadmap-completed">已经完成</h3>
          <div className="roadmap-grid">{COMPLETED.map((item) => <article key={item.title}>
            <span className={item.status === "已验收" ? "roadmap-status accepted" : "roadmap-status"}>{item.status}</span>
            <h4>{item.title}</h4><p>{item.detail}</p>
          </article>)}</div>
          <p className="roadmap-note">“待验收”是开发候选；“初步验收”对应已有整体反馈，特殊分支以测试记录为准。7天未变化是筛选，当前仍由用户选择归档或回收。</p>
        </section>
        <section aria-labelledby="roadmap-planned"><h3 id="roadmap-planned">待开发 · 按优先级推进</h3>
          <ol className="roadmap-plan">{PLANNED.map((item) => <li key={item.title}>
            <span className="roadmap-status planned">{item.priority}</span><h4>{item.title}</h4>
            <p>{item.detail}</p><small>完成标志：{item.done}</small>
          </li>)}</ol>
          <p className="roadmap-note">每次交付一个小单元，经测试和安装验收后继续。视频、运营复盘与完整规则同步排在后续，暂无交付日期。部分能力已有原工作台入口，新界面仍待迁入。</p>
        </section>
      </div>
    </dialog>, document.body)}
  </>;
}
