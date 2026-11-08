import "./globals.css";

export const metadata = {
  title: "tg-userbot · оркестр",
  description: "Облачное зеркало оркестра (Supabase)",
};

export default function RootLayout({ children }) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
