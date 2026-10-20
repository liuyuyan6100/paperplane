import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";

type Summary = { id: string; name: string };
type Profile = Summary & {
  version: number;
  direction: string;
  audience: string;
  goal: string;
};
type Fields = Pick<Profile, "name" | "direction" | "audience" | "goal">;
const blank: Fields = { name: "", direction: "", audience: "", goal: "" };

export function ProfilePanel({
  selectedId,
  profiles,
  onDirty,
}: {
  selectedId: string | null;
  profiles: Summary[];
  onDirty: (value: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [id, setId] = useState<string | null>(null);
  const [fields, setFields] = useState<Fields>(blank);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const client = useQueryClient();
  const detail = useQuery({
    queryKey: ["profile-detail", id],
    queryFn: () => request<Profile>("/profiles/" + id),
    enabled: open && !!id,
  });

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);
  useEffect(() => {
    if (detail.data && !dirty && id === detail.data.id) {
      setFields({
        name: detail.data.name,
        direction: detail.data.direction,
        audience: detail.data.audience,
        goal: detail.data.goal,
      });
    }
  }, [detail.data, dirty, id]);
  useEffect(() => onDirty(dirty || busy), [dirty, busy, onDirty]);
  useEffect(() => () => onDirty(false), [onDirty]);

  function choose(next: string | null) {
    if (dirty && !confirm("画像有未保存输入，切换会放弃这些输入，是否继续？")) return;
    setId(next);
    const cached = next ? client.getQueryData<Profile>(["profile-detail", next]) : null;
    setFields(cached ? {
      name: cached.name,
      direction: cached.direction,
      audience: cached.audience,
      goal: cached.goal,
    } : blank);
    setDirty(false);
    setError("");
    setConflict(false);
    setNotice("");
  }
  function close() {
    if (busy) return;
    if (dirty && !confirm("画像有未保存输入，关闭会放弃这些输入，是否继续？")) return;
    setDirty(false);
    setOpen(false);
  }
  function patch(key: keyof Fields, value: string) {
    setFields((old) => ({ ...old, [key]: value }));
    setDirty(true);
  }
  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    setConflict(false);
    setNotice("");
    try {
      const saved = await request<Profile>(id ? "/profiles/" + id : "/profiles", {
        ...fields,
        ...(id ? { version: detail.data?.version } : {}),
      });
      client.setQueryData(["profile-detail", saved.id], saved);
      setId(saved.id);
      setDirty(false);
      setNotice("画像已保存。文章关联画像仍由上方选择框决定，修改文章后请保存文章。");
      await client.invalidateQueries({ queryKey: ["profiles"] });
    } catch (cause) {
      setError((cause as Error).message);
      setConflict((cause as { status?: number }).status === 409);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        onClick={() => {
          choose(selectedId);
          setOpen(true);
        }}
      >
        管理画像
      </button>
      <dialog
        ref={dialog}
        className="creation-panel profile-panel"
        aria-label="管理账号画像"
        onCancel={(event) => {
          event.preventDefault();
          close();
        }}
      >
        <header>
          <div>
            <h2>管理账号画像</h2>
            <p>本轮只编辑名称、内容方向、目标读者和运营目标。其他画像字段保持原值。</p>
          </div>
          <button onClick={close} disabled={busy}>返回文章</button>
        </header>
        <div className="creation-content profile-editor">
          <label>
            选择画像
            <select
              value={id ?? ""}
              disabled={busy}
              onChange={(event) => choose(event.target.value || null)}
            >
              <option value="">＋ 新建画像</option>
              {profiles.map((profile) => (
                <option key={profile.id} value={profile.id}>{profile.name}</option>
              ))}
            </select>
          </label>
          {detail.error && <p role="alert" className="error">{detail.error.message}</p>}
          {(!id || detail.data?.id === id) && (
            <fieldset disabled={busy}>
              <legend>{id ? `编辑画像 · v${detail.data?.version}` : "新建画像"}</legend>
              <label>画像名称<input aria-label="画像名称" maxLength={80} value={fields.name} onChange={(event) => patch("name", event.target.value)} /></label>
              <label>定位与内容方向<textarea aria-label="定位与内容方向" maxLength={2000} value={fields.direction} onChange={(event) => patch("direction", event.target.value)} /></label>
              <label>目标读者<textarea aria-label="目标读者" maxLength={2000} value={fields.audience} onChange={(event) => patch("audience", event.target.value)} /></label>
              <label>运营目标<textarea aria-label="运营目标" maxLength={2000} value={fields.goal} onChange={(event) => patch("goal", event.target.value)} /></label>
              <button className="primary" disabled={!fields.name.trim() || !dirty} onClick={() => void save()}>保存画像</button>
            </fieldset>
          )}
          {id && !detail.data && !detail.error && <p role="status">正在读取画像…</p>}
          {error && <p role="alert" className="error">{error} 当前输入仍保留，可核对后重试。</p>}
          {conflict && <button onClick={() => {
            if (!confirm("重新读取会放弃当前画像输入，是否继续？")) return;
            setDirty(false);
            setError("");
            setConflict(false);
            void detail.refetch();
          }}>放弃当前输入并重新读取</button>}
          {notice && <p role="status" className="notice">{notice}</p>}
        </div>
        <footer>保存画像不会自动修改当前文章，也不会改变已经启动的任务快照。其他字段可继续在原工作台管理。</footer>
      </dialog>
    </>
  );
}
