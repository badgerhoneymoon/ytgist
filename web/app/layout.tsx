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

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // suppressHydrationWarning covers ONLY this element's attributes. Browser extensions
    // (Immersive Translate) stamp attributes onto <html> before React hydrates; real
    // mismatches inside the app still throw.
    <html lang="en" className={serif.variable} suppressHydrationWarning>
      <body className="antialiased">{children}</body>
    </html>
  );
}
