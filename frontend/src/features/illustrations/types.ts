export interface ImagePage {
  title: string;
  text: string;
  prompt: string;
  slot: "cover" | "body" | "xhs";
  aspect: "landscape" | "portrait" | "square";
  placement?: string;
  caption?: string;
}
export interface ModelIdentity {
  requested_model: string;
  reported_model?: string;
  label: string;
}
export interface Candidate {
  id: string;
  page_index: number;
  status: string;
  image_url: string;
  error: string;
  plan_changed: boolean;
  origin: string;
  instruction: string;
  page: ImagePage;
  model_provenance: ModelIdentity;
}
export interface Plan {
  id: string;
  post_id: string;
  source_version: number;
  post_version: number;
  version: number;
  status: string;
  archived_at: string;
  source_changed: boolean;
  profile_changed: boolean;
  error: string;
  pages: ImagePage[];
  candidates: Candidate[];
  profile: { name: string; visual_style: string };
  references: string[];
  placement_options: { id: string; label: string; excerpt: string }[];
  adopted: { page_index: number; candidate_id: string; image_url: string }[];
  next_models: Record<
    "illustration_plan" | "cover" | "illustration",
    ModelIdentity
  >;
}
export interface ImageAdoption {
  candidate_id: string;
  post_version: number;
  placement: string;
  caption: string;
  ack_changes: boolean;
}
export interface ImagePreview {
  post_version: number;
  preview_html: string;
  cover: string;
  placement_label: string;
}
export const imageStatus: Record<string, string> = {
  review: "待审阅",
  queued: "排队中",
  running: "生成中",
  failed: "失败",
  interrupted: "已中断",
};
export const editablePages = (pages: ImagePage[]): ImagePage[] =>
  pages.map((p) => ({
    title: p.title,
    text: p.text,
    prompt: p.prompt,
    slot: p.slot,
    aspect: p.aspect,
    placement: p.placement ?? (p.slot === "body" ? "legacy_end" : ""),
    caption: p.caption ?? "",
  }));
