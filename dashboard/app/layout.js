import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "tg-userbot · панель управления",
  description: "Панель управления рассылками userbot через Telegram Mini App",
  applicationName: "tg-userbot",
  robots: { index: false, follow: false },
};

export const viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0e14" },
    { media: "(prefers-color-scheme: light)", color: "#ffffff" },
  ],
};

export default function RootLayout({ children }) {
  return (
    // suppressHydrationWarning: до гидрации Telegram-скрипт вешает
    // --tg-viewport-* на <html>, а браузерные расширения (Dark Reader) —
    // свои data-атрибуты; это не наши изменения и патчить их не нужно.
    <html lang="ru" suppressHydrationWarning>
      <body>
        <Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive" />
        {children}
      </body>
    </html>
  );
}
