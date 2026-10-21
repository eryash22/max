"use client";

import { useEffect, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "../hooks/useAuth";
import { Sidebar } from "./Sidebar";
import { TopNav } from "./TopNav";
import { LoadingState } from "./LoadingState";
import { ErrorState } from "./ErrorState";

/**
 * `requiredPermission` is an opt-in convenience — the backend endpoint the page
 * calls is always the real authorization boundary (RBAC_MATRIX.md). This only
 * saves each page from repeating its own "if (!hasPermission(...)) return
 * <ErrorState .../>" boilerplate (see app/users/page.tsx's prior pattern) and
 * gives a consistent "you don't have permission" state instead of a confusing
 * empty page or a raw 403 from a failed API call. Deliberately not a central
 * route->permission table — that would drift out of sync with the routers the
 * moment either side changed; each page still states its own requirement.
 */
export function ProtectedShell({
  children,
  requiredPermission,
}: {
  children: ReactNode;
  requiredPermission?: string;
}) {
  const { user, loading, hasPermission } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!loading && !user) {
      router.replace("/login");
    }
  }, [loading, user, router]);

  if (loading) {
    return <LoadingState label="Checking session..." />;
  }
  if (!user) {
    return null;
  }

  const denied = requiredPermission !== undefined && !hasPermission(requiredPermission);

  return (
    <div className="app-shell">
      <Sidebar />
      <div className="app-shell__main">
        <TopNav />
        <main className="app-shell__content">
          {denied ? <ErrorState message="You do not have permission to view this page." /> : children}
        </main>
      </div>
    </div>
  );
}
