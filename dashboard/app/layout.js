import "./globals.css";

export const metadata = {
  title: "tg-userbot · мини-ап",
  description: "Панель управления рассылками userbot через Telegram Mini App",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <head>
        <script src="https://telegram.org/js/telegram-web-app.js" />
      </head>
      <body>{children}</body>
    </html>
  );
}
