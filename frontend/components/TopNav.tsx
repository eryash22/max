"use client";

import { useAuth } from "../hooks/useAuth";

export function TopNav() {
  const { user, logout } = useAuth();
  return (
    <header className="top-nav">
      <div />
      <div className="top-nav__user">
        {user && (
          <>
            <span className="top-nav__name">{user.name}</span>
            <span className="top-nav__roles">{user.roles.join(", ")}</span>
            <button className="btn btn--ghost" onClick={logout}>
              Sign out
            </button>
          </>
        )}
      </div>
    </header>
  );
}
