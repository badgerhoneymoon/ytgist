import type { Metadata } from "next";
import { PT_Serif } from "next/font/google";
import "./globals.css";

/** TWO LAYERS, deliberately.
 *
 *  Serif = the READING layer (TL;DR, step bodies, evidence). Sans = the INSTRUMENT layer
 *  (title, headlines, numerals, timestamps, buttons, meta). Serif says "argument"; sans
 *  says "tool".
 *
 *  PT Serif specifically because most editorial serifs — Newsreader, Fraunces — are
 *  Latin-only, and this app reads Russian more often than English. PT Serif is
 *  ParaType's: Cyrillic is its native writing system, not a bolted-on subset. Verified
 *  against the Google Fonts API that it actually ships cyrillic + cyrillic-ext.
 */
const serif = PT_Serif({
  subsets: ["latin", "cyrillic"],
  weight: ["400", "700"],
  style: ["normal", "italic"],
  display: "swap",
  variable: "--font-serif",
});

export const metadata: Metadata = {
  title: "ytgist",
  description: "Paste a YouTube link, get the argument.",
};

// Rendered per request, so the engine the launcher chose (YTGIST_ENGINE, read at RUN time)
// reaches the browser even from a production build, where NEXT_PUBLIC_* would be baked in.
export const dynamic = "force-dynamic";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  const engine = JSON.stringify(process.env.YTGIST_ENGINE || "");
  return (
    // suppressHydrationWarning covers ONLY this element's attributes. Browser extensions
    // (Immersive Translate) stamp attributes onto <html> before React hydrates; real
    // mismatches inside the app still throw.
    <html lang="en" className={serif.variable} suppressHydrationWarning>
      <head>
        {/* Runs before the app's scripts: a window switched to the Mac (the PC was busy)
            talks to the Mac from its first fetch. See engine.ts. */}
        <script dangerouslySetInnerHTML={{ __html:
          `window.__YTGIST_PC__=${engine};window.__YTGIST_ENGINE__=${engine};` +
          `try{if(window.__YTGIST_PC__&&sessionStorage.getItem("ytgist-on")==="mac")window.__YTGIST_ENGINE__=""}catch(e){}` }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
