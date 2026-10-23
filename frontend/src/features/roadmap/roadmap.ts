// Update this public progress snapshot together with ROADMAP.md and the user guide.
export const ROADMAP_UPDATED = "2026-10-23";
export const PRODUCT_AUTHOR = "灵魂工程师";
export const COMPLETED = [
  { title: "文章与版本", detail: "编辑、预览、保存重开、版本保护、归档与回收站", status: "已验收" },
  { title: "侧边 AI", detail: "讨论、全文或选区修改、对照、采用与撤销", status: "已验收" },
  { title: "素材 → 提纲 → 正文", detail: "选用资料，确认提纲，审阅并采用正文", status: "已验收" },
  { title: "封面与正文配图", detail: "生成或上传、位置预览、采用保存；失败不占候选卡片", status: "已验收" },
  { title: "账号画像", detail: "名称、内容方向、目标读者、运营目标四个核心字段", status: "已验收" },
  { title: "功能进度与规划", detail: "独立侧边栏入口，展示已完成、验收状态与待开发优先级", status: "已验收" },
  { title: "图文检查与公众号草稿", detail: "预览进入检查、补齐建议审阅、保存后继续、草稿回执与任务核对", status: "待验收" },
  { title: "选题、视觉入门与任务收纳", detail: "三个选题候选；四段入门流程；单条/批量归档、回收站及7天未变化筛选", status: "初步验收" },
];
export const PLANNED = [
  { priority: "P1 · 本轮验收后", title: "同一母稿的多平台改编", detail: "每次只迁入一个平台的改编与审阅入口，保留母稿和独立版本。", done: "可生成、审阅并保存平台稿，原文和各平台状态互不覆盖。" },
  { priority: "P2", title: "资料与已确认写作经验", detail: "补齐资料管理、经验确认/纠错/停用；长期规则与冲突处理分步推进。", done: "能查来源、改错和停用，候选建议不会自动成为写作事实。" },
  { priority: "M5 · 稳定交付", title: "安装升级、备份与回退", detail: "完整数据兼容和升级演练，再完成稳定版验收。", done: "旧文章、媒体、配置、任务回执可验证地保留并能回退。" },
];
