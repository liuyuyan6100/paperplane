import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";

type Preferences = { close_behavior: "ask" | "tray" | "exit" };

export function DesktopSettings() {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const preferences = useQuery({
    queryKey: ["desktop-preferences"],
    queryFn: () => request<Preferences>("/preferences"),
    enabled: open,
    staleTime: 0,
  });
  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  async function save(close_behavior: Preferences["close_behavior"]) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await request<Preferences>("/preferences", {
        close_behavior,
      });
      client.setQueryData(["desktop-preferences"], saved);
      setNotice("关闭习惯已保存，下一次关闭窗口生效。");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button onClick={() => setOpen(true)}>设置</button>
      <dialog
        ref={dialog}
        className="desktop-settings"
        aria-label="设置"
        onCancel={(event) => {
          event.preventDefault();
          if (!busy) setOpen(false);
        }}
      >
        <header>
          <h2>设置</h2>
          <button disabled={busy} onClick={() => setOpen(false)}>
            关闭设置
          </button>
        </header>
        <label>
          关闭窗口行为
          <select
            aria-label="关闭窗口行为"
            disabled={!preferences.data || busy}
            value={preferences.data?.close_behavior ?? "ask"}
            onChange={(event) =>
              void save(event.target.value as Preferences["close_behavior"])
            }
          >
            <option value="ask">下次关闭时询问并记住选择</option>
            <option value="tray">缩到托盘继续运行</option>
            <option value="exit">完全退出</option>
          </select>
        </label>
        <p>
          选择后立即保存。首次关闭选择托盘或退出后，会一直沿用，直到在这里修改。
        </p>
        <p>
          有未保存输入、无法读取保存状态或退出会中断任务时，仍会提醒。托盘菜单的“退出验证版”始终用于主动完全退出。
        </p>
        {busy && <p role="status">正在保存设置…</p>}
        {notice && <p role="status">{notice}</p>}
        {(error || preferences.error) && (
          <p role="alert">{error || preferences.error?.message}</p>
        )}
      </dialog>
    </>
  );
}
