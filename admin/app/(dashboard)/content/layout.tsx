/**
 * 内容创作模块的局部主题包装。
 *
 * 设计取舍:
 * 这部分页面是从原本独立的视频工作流项目接进来的, 代码里大量使用了 text-accent / bg-accent
 * 假设 accent 是个鲜亮的品牌色。但 tt shop 整体走中性灰 + 极克制配色, accent = 灰。
 * 直接迁移导致 badge、icon 在暗色背景上几乎看不见 (用户反馈)。
 *
 * 不动每个组件、只在这一层用 .workflow-theme 局部重写 --accent 为亮 sky 蓝,
 * 既保留原工作流的视觉, 又不污染电商核心页面。
 */
export default function ContentLayout({ children }: { children: React.ReactNode }) {
  return <div className="workflow-theme">{children}</div>;
}
