import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Tickipate",
  description: "Event-driven stock research. Research, not investment advice.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
