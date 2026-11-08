import "./globals.css";

export const metadata = {
  title: "tg-userbot · мини-ап",
  description: "Панель управления рассылками userbot через Telegram Mini App",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
