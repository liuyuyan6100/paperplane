import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { request } from "../../api";

export interface ModelSettingsData {
  connection: { base_url: string; model: string; key_set: boolean };
  text_tasks: { id: string; model: string; effective_model: string }[];
  image_tasks: { id: string; model: string; effective_model: string }[];
  image: {
    use_shared: boolean;
    base_url: string;
    model: string;
    key_set: boolean;
    references: "edits" | "none";
  };
  profiles: {
    id: string;
    name: string;
    visual_style: string;
    reference_count: number;
  }[];
}
export interface Catalog {
  models: string[];
}

export function ModelSettings({ onClose }: { onClose: () => void }) {
  const query = useQuery({
    queryKey: ["model-settings"],
    queryFn: () => request<ModelSettingsData>("/assistant/settings"),
    refetchOnWindowFocus: false,
  });
  return (
    <div className="modal-backdrop">
      <section
        className="settings-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="模型与生图设置"
      >
        {query.data ? (
          <SettingsForm initial={query.data} onClose={onClose} />
        ) : (
          <>
            <h2>模型与生图设置</h2>
            <p role="status">{query.error?.message || "读取已保存配置…"}</p>
            <button onClick={onClose}>关闭设置</button>
          </>
        )}
      </section>
    </div>
  );
}

function SettingsForm({
  initial,
  onClose,
}: {
  initial: ModelSettingsData;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [connection, setConnection] = useState({
    ...initial.connection,
    api_key: "",
  });
  const [text, setText] = useState({
    chat: initial.text_tasks.find((t) => t.id === "chat")?.model || "",
    body: initial.text_tasks.find((t) => t.id === "body")?.model || "",
  });
  const [image, setImage] = useState({
    ...initial.image,
    api_key: "",
    cover_model: initial.image_tasks.find((t) => t.id === "cover")?.model || "",
    illustration_model:
      initial.image_tasks.find((t) => t.id === "illustration")?.model || "",
  });
  const [expanded, setExpanded] = useState(!initial.connection.key_set);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [dirty, setDirty] = useState(false);
  const first = useRef<HTMLButtonElement>(null);
  const catalog = useQuery({
    queryKey: ["model-catalog", "text"],
    queryFn: () => request<Catalog>("/assistant/models"),
    retry: false,
    staleTime: 60000,
  });
  const images = useQuery({
    queryKey: ["model-catalog", "image"],
    queryFn: () => request<Catalog>("/assistant/models?kind=image"),
    retry: false,
    staleTime: 60000,
  });
  const close = () => {
    if (
      !busy &&
      (!dirty || confirm("模型配置尚未保存，关闭并放弃这些配置修改？"))
    )
      onClose();
  };
  useEffect(() => {
    first.current?.focus();
  }, []);
  async function save() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await request("/assistant/settings", {
        connection: {
          mode: "api",
          base_url: connection.base_url,
          model: connection.model,
          ...(connection.api_key ? { api_key: connection.api_key } : {}),
        },
        text_models: text,
        image: {
          use_shared: image.use_shared,
          base_url: image.base_url,
          model: image.model,
          cover_model: image.cover_model,
          illustration_model: image.illustration_model,
          references: image.references,
          ...(image.api_key ? { api_key: image.api_key } : {}),
        },
      });
      await Promise.all([
        client.invalidateQueries({ queryKey: ["assistant-configuration"] }),
        client.invalidateQueries({ queryKey: ["model-settings"] }),
        client.invalidateQueries({ queryKey: ["model-catalog"] }),
      ]);
      onClose();
    } catch (failure) {
      setError((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
      onChange={() => setDirty(true)}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <header>
        <div>
          <h2>模型与生图设置</h2>
          <p>
            沿用当前验证版已有配置，密钥留空即保留。更改只影响之后发起的请求。
          </p>
        </div>
        <button ref={first} type="button" disabled={busy} onClick={close}>
          关闭设置
        </button>
      </header>
      <div className="settings-body">
        <fieldset>
          <legend>文字模型</legend>
          <label>
            默认文字模型
            <input
              aria-label="默认文字模型"
              list="text-models"
              value={connection.model}
              onChange={(e) =>
                setConnection({ ...connection, model: e.target.value })
              }
            />
          </label>
          <label>
            讨论默认模型
            <input
              aria-label="讨论默认模型"
              list="text-models"
              placeholder="跟随默认文字模型"
              value={text.chat}
              onChange={(e) => setText({ ...text, chat: e.target.value })}
            />
          </label>
          <label>
            修改默认模型
            <input
              aria-label="修改默认模型"
              list="text-models"
              placeholder="跟随默认文字模型"
              value={text.body}
              onChange={(e) => setText({ ...text, body: e.target.value })}
            />
          </label>
          <datalist id="text-models">
            {catalog.data?.models.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
          <small>
            {catalog.error
              ? catalog.error.message
              : `已识别 ${catalog.data?.models.length ?? 0} 个目录模型；目录根据已保存接口读取，改地址后请先保存；目录不代表调用已验证。`}
          </small>
        </fieldset>
        <fieldset>
          <legend>生图模型</legend>
          <label>
            图片连接
            <select
              aria-label="图片连接"
              value={image.use_shared ? "shared" : "independent"}
              onChange={(e) =>
                setImage({ ...image, use_shared: e.target.value === "shared" })
              }
            >
              <option value="shared">复用文字接口</option>
              <option value="independent">沿用独立图片接口</option>
            </select>
          </label>
          <label>
            默认生图模型
            <input
              aria-label="默认生图模型"
              list="image-models"
              value={image.model}
              onChange={(e) => setImage({ ...image, model: e.target.value })}
            />
          </label>
          <label>
            封面模型
            <input
              list="image-models"
              placeholder="跟随默认生图模型"
              value={image.cover_model}
              onChange={(e) =>
                setImage({ ...image, cover_model: e.target.value })
              }
            />
          </label>
          <label>
            正文插图模型
            <input
              list="image-models"
              placeholder="跟随默认生图模型"
              value={image.illustration_model}
              onChange={(e) =>
                setImage({ ...image, illustration_model: e.target.value })
              }
            />
          </label>
          <label>
            参考图能力
            <select
              value={image.references}
              onChange={(e) =>
                setImage({
                  ...image,
                  references: e.target.value as "edits" | "none",
                })
              }
            >
              <option value="edits">支持参考图编辑</option>
              <option value="none">仅文字生图</option>
            </select>
          </label>
          <datalist id="image-models">
            {images.data?.models.map((model) => (
              <option key={model} value={model} />
            ))}
          </datalist>
          {images.error && (
            <small>{images.error.message}；已保存的生图模型继续保留。</small>
          )}
        </fieldset>
        <fieldset>
          <legend>账号画像与生图风格</legend>
          <p>
            风格随文章所选画像生效。生图继续在原工作台的图稿功能中使用这些设置。
          </p>
          {initial.profiles.length ? (
            initial.profiles.map((profile) => (
              <details key={profile.id}>
                <summary>
                  {profile.name} ·{" "}
                  {profile.visual_style ? "已保存视觉风格" : "未设置视觉风格"} ·{" "}
                  {profile.reference_count} 张参考图
                </summary>
                <p>{profile.visual_style || "可在账号画像中添加风格。"}</p>
              </details>
            ))
          ) : (
            <p>当前没有画像配置。</p>
          )}
          <a href="/#profiles" target="_blank" rel="noopener noreferrer">
            管理账号画像与风格 ↗
          </a>
          <p>
            画像管理在独立页面打开，当前编辑稿保留。保存画像后，关闭并重新打开设置可刷新。
          </p>
        </fieldset>
        <button
          type="button"
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? "收起接口与密钥设置" : "接口与密钥设置"}
        </button>
        {expanded && (
          <fieldset>
            <legend>接口连接</legend>
            <label>
              文字接口地址
              <input
                value={connection.base_url}
                onChange={(e) =>
                  setConnection({ ...connection, base_url: e.target.value })
                }
              />
            </label>
            <label>
              文字 API Key
              <input
                type="password"
                autoComplete="new-password"
                placeholder={
                  initial.connection.key_set ? "已保存，留空保留" : "尚未保存"
                }
                value={connection.api_key}
                onChange={(e) =>
                  setConnection({ ...connection, api_key: e.target.value })
                }
              />
            </label>
            <label>
              独立图片接口地址
              <input
                value={image.base_url}
                onChange={(e) =>
                  setImage({ ...image, base_url: e.target.value })
                }
              />
            </label>
            <label>
              图片 API Key
              <input
                type="password"
                autoComplete="new-password"
                placeholder={
                  initial.image.key_set ? "已保存，留空保留" : "尚未保存"
                }
                value={image.api_key}
                onChange={(e) =>
                  setImage({ ...image, api_key: e.target.value })
                }
              />
            </label>
          </fieldset>
        )}
        {error && (
          <p role="alert" className="assistant-error">
            {error}
          </p>
        )}
      </div>
      <footer>
        <span>文章与对话保留在当前页面。</span>
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "保存中…" : "保存模型设置"}
        </button>
      </footer>
    </form>
  );
}
