import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "PAPSrun | 맞춤형 캠퍼스 러닝 코스 최적화 플랫폼",
  description: "지구과학 미기상 · 체육 생체역학 ML · 수학 다익스트라 · AI 윤리(XAI) 통합 시스템",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-slate-50 text-slate-900 antialiased">
        {children}
      </body>
    </html>
  );
}