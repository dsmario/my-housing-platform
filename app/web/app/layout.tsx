import type { ReactNode } from "react";

export const metadata = {
  title: "My Housing Platform",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: "3rem auto", maxWidth: "40rem", padding: "0 1rem" }}>
        {children}
      </body>
    </html>
  );
}
