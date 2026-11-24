import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Game Code Hub · 后台",
  description: "Game Code Hub 内部运营管理系统",
  robots: { index: false, follow: false },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" className="dark" suppressHydrationWarning>
      <head>
        {/* Inter + JetBrains Mono：与设计 token 对齐 */}
        <link rel="preconnect" href="https://rsms.me/" />
        <link rel="stylesheet" href="https://rsms.me/inter/inter.css" />
        <link
          rel="stylesheet"
          href="https://cdn.jsdelivr.net/gh/JetBrains/JetBrainsMono@2.304/web/jetbrains-mono.css"
        />
      </head>
      <body className="min-h-screen bg-background text-foreground antialiased">
        {children}
      </body>
    </html>
  );
}
