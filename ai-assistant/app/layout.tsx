import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./components/Providers";
import { AppShell } from "./components/AppShell";

export const metadata: Metadata = {
  title: "AI Assistant",
  description: "Home automation AI assistant",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
