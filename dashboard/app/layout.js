import "./globals.css";
import Script from "next/script";

export const metadata = {
  title: "tg-userbot · мини-ап",
  description: "Панель управления рассылками userbot через Telegram Mini App",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>
        <Script src="https://telegram.org/js/telegram-web-app.js" strategy="beforeInteractive" />
        {children}
      </body>
    </html>
  );
}
