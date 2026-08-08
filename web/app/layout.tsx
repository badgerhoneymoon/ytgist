import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "ytgist",
  description: "Paste a YouTube link, get the argument.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning is scoped to THIS element's attributes only — real
    // mismatches inside the app still throw. It is here because browser extensions
    // stamp attributes onto <html> before React hydrates: Immersive Translate adds
    // data-immersive-translate-page-theme, which React then reports as a server/client
    // mismatch we neither caused nor can prevent. This is the fix React's own error
    // page recommends for that case.
    <html lang="en" suppressHydrationWarning>
      <body className="antialiased [font-family:-apple-system,BlinkMacSystemFont,sans-serif]">
        {children}
      </body>
    </html>
  );
}
