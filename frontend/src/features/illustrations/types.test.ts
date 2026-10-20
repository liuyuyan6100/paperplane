import { describe, expect, it } from "vitest";
import { pageCandidateDisplay, type Candidate } from "./types";

const candidate = (id: string, status: string, page_index = 0): Candidate => ({
  id,
  page_index,
  status,
  image_url: status === "review" ? "/image.png" : "",
  error: status === "failed" ? "请求超时" : "",
  plan_changed: false,
  origin: "ai",
  instruction: "",
  page: { title: "封面", text: "", prompt: "", slot: "cover", aspect: "landscape" },
  model_provenance: { requested_model: "image-model", label: "image-model" },
});

describe("候选图区显示", () => {
  it("失败保留在任务数据，但不占候选卡片；新任务发起后隐藏旧失败提示", () => {
    const attempts = [candidate("old", "review"), candidate("failed", "failed")];
    expect(pageCandidateDisplay(attempts, 0).visible.map((item) => item.id)).toEqual(["old"]);
    expect(pageCandidateDisplay(attempts, 0).latestFailure?.error).toBe("请求超时");
    attempts.push(candidate("retry", "running"));
    expect(pageCandidateDisplay(attempts, 0).visible.map((item) => item.id)).toEqual(["old", "retry"]);
    expect(pageCandidateDisplay(attempts, 0).latestFailure).toBeNull();
  });

  it("仅显示当前页的可用候选，其他页失败不影响当前页", () => {
    const result = pageCandidateDisplay([candidate("failed", "failed"), candidate("body", "review", 1)], 1);
    expect(result.visible.map((item) => item.id)).toEqual(["body"]);
    expect(result.latestFailure).toBeNull();
  });
});
