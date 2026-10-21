import type { ReactNode } from "react";
import { AuthProvider } from "../hooks/useAuth";
import { ThemeProvider } from "../components/ThemeProvider";
import "../styles/globals.css";

export const metadata = {
  title: "MAX Biomedical AI",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <ThemeProvider>
          <AuthProvider>{children}</AuthProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
