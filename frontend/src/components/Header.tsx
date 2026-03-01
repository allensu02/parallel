"use client";

import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { LogOut, Mail, Loader2 } from "lucide-react";
import { getAuthStatus, startBrowserLogin, logout, type AuthStatus } from "@/lib/api";

interface HeaderProps {
  onAuthChange?: (authenticated: boolean) => void;
}

export default function Header({ onAuthChange }: HeaderProps) {
  const [auth, setAuth] = useState<AuthStatus | null>(null);
  const [loginPending, setLoginPending] = useState(false);

  const checkAuth = useCallback(async () => {
    try {
      const status = await getAuthStatus();
      setAuth(status);
      onAuthChange?.(status.authenticated);
      if (status.authenticated && loginPending) {
        setLoginPending(false);
      }
      return status.authenticated;
    } catch {
      setAuth({ authenticated: false, email: null });
      onAuthChange?.(false);
      return false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  // Re-check when loginPending changes (for Playwright flow)
  useEffect(() => {
    if (loginPending) checkAuth();
  }, [loginPending, checkAuth]);

  useEffect(() => {
    if (!loginPending) return;
    const timer = setInterval(async () => {
      const done = await checkAuth();
      if (done) {
        setLoginPending(false);
        clearInterval(timer);
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [loginPending, checkAuth]);

  const handleLogin = async () => {
    try {
      setLoginPending(true);
      const data = await startBrowserLogin();
      if (data.auth_url) {
        // OAuth flow — redirect to Google consent screen
        window.location.href = data.auth_url;
        return;
      }
      // Playwright fallback — keep polling for auth status
    } catch {
      alert("Failed to start login. Make sure the backend is running.");
      setLoginPending(false);
    }
  };

  const handleLogout = async () => {
    await logout();
    setAuth({ authenticated: false, email: null });
    onAuthChange?.(false);
  };

  return (
    <motion.header
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="glass-panel flex items-center justify-between px-5 py-3 mx-4 mt-3"
    >
      <div className="flex items-center gap-4">
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-golden">Parallel</h1>
          <p className="text-[11px] text-muted -mt-0.5">Automation Console</p>
        </div>
        <span className="text-[10px] text-primary-light font-semibold font-mono px-2.5 py-1 rounded-md bg-primary/10 border border-primary/30">
          live orchestration
        </span>
      </div>

      <div className="flex items-center gap-3">
        {auth?.authenticated ? (
          <>
            <div className="flex items-center gap-2 text-sm">
              <div className="w-2.5 h-2.5 rounded-full bg-success animate-pulse" />
              <Mail className="w-4 h-4 text-primary-light" />
              <span className="text-foreground/90 font-medium">{auth.email || "Connected"}</span>
            </div>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-2/70 border border-border hover:border-primary/40 transition-all text-muted hover:text-foreground"
            >
              <LogOut className="w-3 h-3" />
              Sign out
            </button>
          </>
        ) : loginPending ? (
          <div className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-primary/10 border border-primary/25 text-primary-light animate-border-warm">
            <Loader2 className="w-4 h-4 animate-spin text-primary-light" />
            <span className="font-semibold">Waiting for login...</span>
          </div>
        ) : (
          <motion.button
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={handleLogin}
            className="flex items-center gap-2 px-5 py-2 text-sm rounded-lg bg-gradient-to-r from-primary to-primary-light transition-all text-background font-bold"
          >
            <Mail className="w-4 h-4" />
            Connect Gmail
          </motion.button>
        )}
      </div>
    </motion.header>
  );
}
