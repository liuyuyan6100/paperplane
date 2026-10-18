import { useState } from "react";

export function ModelPicker({
  label,
  value,
  models,
  emptyLabel,
  onChange,
}: {
  label: string;
  value: string;
  models: string[];
  emptyLabel: string;
  onChange: (value: string) => void;
}) {
  const [manual, setManual] = useState(false);
  const options = [...new Set([...models, ...(value ? [value] : [])])];
  return (
    <div className="model-picker">
      <label>
        <span>{label}</span>
        {manual ? (
          <input
            aria-label={label}
            value={value}
            placeholder="填写接口支持的模型名称"
            onChange={(event) => onChange(event.target.value)}
          />
        ) : (
          <select
            aria-label={label}
            value={value}
            onChange={(event) => onChange(event.target.value)}
          >
            <option value="">{emptyLabel}</option>
            {options.map((model) => (
              <option key={model} value={model}>
                {model}
                {!models.includes(model) ? "（已保存，目录未列出）" : ""}
              </option>
            ))}
          </select>
        )}
      </label>
      <button
        type="button"
        aria-label={`${label}：${manual ? "从目录选择" : "手动填写"}`}
        onClick={() => setManual(!manual)}
      >
        {manual ? "从目录选择" : "手动填写"}
      </button>
    </div>
  );
}
