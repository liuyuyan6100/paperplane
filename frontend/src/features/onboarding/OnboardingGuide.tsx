import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { GUIDE_PHASES, type GuideAction } from "./flow";

export function OnboardingGuide({
  hasArticle,
  onAction,
  mobile = false,
}: {
  hasArticle: boolean;
  onAction: (action: GuideAction) => void;
  mobile?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [phaseId, setPhaseId] = useState(GUIDE_PHASES[0].id);
  const [stepId, setStepId] = useState(GUIDE_PHASES[0].steps[0].id);
  const dialog = useRef<HTMLDialogElement>(null);
  const phase = GUIDE_PHASES.find((item) => item.id === phaseId) ?? GUIDE_PHASES[0];
  const step = phase.steps.find((item) => item.id === stepId) ?? phase.steps[0];

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);

  function choosePhase(id: string) {
    const next = GUIDE_PHASES.find((item) => item.id === id)!;
    setPhaseId(id);
    setStepId(next.steps[0].id);
  }

  function go() {
    setOpen(false);
    requestAnimationFrame(() => onAction(step.action));
  }

  return <>
    <button className={"onboarding-entry" + (mobile ? " onboarding-mobile" : "")}
      onClick={() => setOpen(true)}>新手入门</button>
    {createPortal(<dialog ref={dialog} className="onboarding-guide" aria-label="新手入门流程"
      onCancel={(event) => { event.preventDefault(); setOpen(false); }}>
      <header className="onboarding-header">
        <div>
          <span className="milestone">纸飞机 · 从想法到图文</span>
          <h2>跟着流程，完成第一篇文章</h2>
          <p>点击流程节点查看入口和完成标志。每一步由你决定是否执行。</p>
        </div>
        <button onClick={() => setOpen(false)}>返回编辑器</button>
      </header>
      <div className="onboarding-body">
        <nav className="onboarding-map" aria-label="创作流程">
          {GUIDE_PHASES.map((item, index) => <div className="onboarding-route" key={item.id}>
            <button className={`onboarding-phase ${item.tone}`}
              aria-current={phase.id === item.id ? "step" : undefined}
              onClick={() => choosePhase(item.id)}>
              <span className="onboarding-number">{item.number}</span>
              <span className="onboarding-phase-title">{item.title}</span>
              <small>{item.cue}</small>
              <span className="onboarding-dots" aria-hidden="true">
                {item.steps.map((substep) => <i key={substep.id} />)}
              </span>
            </button>
            {index < GUIDE_PHASES.length - 1 && <span className="onboarding-arrow" aria-hidden="true">→</span>}
          </div>)}
        </nav>
        <div className="onboarding-detail">
          <div className="onboarding-step-list">
            <h3>{phase.number} · {phase.title}</h3>
            {phase.steps.map((item, index) => <button key={item.id}
              className={step.id === item.id ? "selected" : ""}
              aria-pressed={step.id === item.id}
              onClick={() => setStepId(item.id)}>
              <span>{index + 1}</span>{item.title}
            </button>)}
          </div>
          <section className="onboarding-step-detail" aria-live="polite">
            <small>当前步骤</small>
            <h3>{step.title}</h3>
            <div className="onboarding-signpost">
              <span>入口</span><strong>{step.entry}</strong>
            </div>
            <div className="onboarding-signpost complete">
              <span>完成标志</span><strong>{step.done}</strong>
            </div>
            {step.needsArticle && !hasArticle &&
              <p role="status">先创建并保存文章，之后再进入这一步。</p>}
            <button className="primary" onClick={go}
              disabled={!!step.needsArticle && !hasArticle}>
              前往这一步 →
            </button>
          </section>
        </div>
        <p className="onboarding-note">生成可能产生模型费用；提纲、正文和图片都先审阅再采用。任务中心只显示状态，不会自动发布文章。</p>
      </div>
    </dialog>, document.body)}
  </>;
}
