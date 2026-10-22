export type GuideAction =
  | "model" | "profile" | "topic" | "article" | "creation"
  | "images" | "preview" | "tasks" | "roadmap";

export type GuideStep = {
  id: string;
  title: string;
  entry: string;
  done: string;
  action: GuideAction;
  needsArticle?: boolean;
};

export type GuidePhase = {
  id: string;
  number: string;
  title: string;
  cue: string;
  tone: string;
  steps: GuideStep[];
};

// Keep this map aligned with visible editor entries and docs/feature-guides/quick-start.md.
export const GUIDE_PHASES: GuidePhase[] = [
  {
    id: "prepare", number: "01", title: "准备", cue: "让 AI 认识你的账号",
    tone: "blue", steps: [
      { id: "model", title: "配置文字模型", entry: "侧边 AI → 模型与生图设置", done: "文字模型完成自测", action: "model" },
      { id: "profile", title: "建立账号画像", entry: "文章信息 → 管理画像", done: "画像已保存，可用于本篇", action: "profile" },
    ],
  },
  {
    id: "idea", number: "02", title: "选题", cue: "先选角度，再写文章",
    tone: "violet", steps: [
      { id: "topic", title: "生成并选定题目", entry: "顶部 → 帮我选题", done: "选定一个候选，打开空白文章", action: "topic" },
      { id: "article", title: "保存文章", entry: "文章标题、正文 → 保存文章", done: "出现已保存版本", action: "article" },
    ],
  },
  {
    id: "create", number: "03", title: "创作", cue: "每个结果先审阅",
    tone: "amber", steps: [
      { id: "creation", title: "素材、提纲与正文", entry: "文章顶部「素材与创作」按钮；已有任务会打开待确认提纲", done: "确认提纲，审阅并采用正文", action: "creation", needsArticle: true },
    ],
  },
  {
    id: "finish", number: "04", title: "成稿", cue: "配图、预览、查任务",
    tone: "green", steps: [
      { id: "images", title: "封面与正文配图", entry: "顶部 → 封面与配图", done: "图片经审阅采用", action: "images", needsArticle: true },
      { id: "preview", title: "查看整篇图文", entry: "顶部 → 预览", done: "核对文字与图片位置", action: "preview", needsArticle: true },
      { id: "tasks", title: "查看任务与收纳记录", entry: "顶部 → 任务中心", done: "处理失败项；废弃项归档或移入可恢复回收站", action: "tasks" },
      { id: "roadmap", title: "了解功能进度", entry: "侧边栏 → 功能进度与规划", done: "区分已完成、初步验收和待开发功能", action: "roadmap" },
    ],
  },
];
