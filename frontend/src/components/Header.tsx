"use client";

import { useEffect, useState, useCallback } from "react";
import { motion } from "framer-motion";
import { Hexagon, LogOut, Mail, Loader2 } from "lucide-react";
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
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="glass-panel flex items-center justify-between px-6 py-3.5 mx-4 mt-3 relative overflow-hidden honey-drip-edge"
      style={{ borderRadius: 16 }}
    >
      {/* Honeycomb bg in header */}
      <div className="absolute inset-0 honeycomb-bg opacity-40 pointer-events-none" />
      {/* Warm glow in top-left */}
      <div className="absolute top-0 left-0 w-64 h-32 pointer-events-none" style={{ background: "radial-gradient(ellipse at top left, rgba(232,163,23,0.08) 0%, transparent 70%)" }} />

      <div className="flex items-center gap-3 relative z-10">
        {/* Hex logo — animated glow + bounce */}
        <motion.div
          animate={{ rotate: [0, 5, -5, 0], scale: [1, 1.04, 1] }}
          transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
          className="w-11 h-11 hex-badge bg-gradient-to-br from-honey via-primary to-primary-dark shadow-xl shadow-honey/30"
        >
          <Hexagon className="w-6 h-6 text-background" strokeWidth={2.5} />
        </motion.div>
        <div>
          <h1 className="text-2xl font-extrabold tracking-tight text-golden">Hive</h1>
        </div>
        <span className="text-[10px] text-honey font-bold font-mono px-3 py-1 rounded-full bg-honey/10 border border-honey/25 animate-border-warm">
          swarm intelligence
        </span>
      </div>

      <div className="flex items-center gap-3 relative z-10">
        {auth?.authenticated ? (
          <>
            <div className="flex items-center gap-2 text-sm">
              <div className="w-2.5 h-2.5 rounded-full bg-success animate-pulse shadow-md shadow-success/30" />
              <Mail className="w-4 h-4 text-honey" />
              <span className="text-foreground/90 font-medium">{auth.email || "Connected"}</span>
            </div>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg bg-surface-2/60 border border-honey/20 hover:border-honey/40 hover-glow transition-all text-muted hover:text-foreground"
            >
              <LogOut className="w-3 h-3" />
              Sign out
            </button>
          </>
        ) : loginPending ? (
          <div className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-honey/10 border border-honey/25 text-honey animate-border-warm">
            <Loader2 className="w-4 h-4 animate-spin text-honey" />
            <span className="font-semibold">Waiting for login...</span>
          </div>
        ) : (
          <motion.button
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
            onClick={handleLogin}
            className="flex items-center gap-2 px-6 py-2.5 text-sm rounded-xl bg-gradient-to-r from-primary via-honey to-primary-light hover:from-honey hover:to-honey transition-all text-background font-extrabold shadow-xl shadow-honey/35 hover:shadow-honey/50"
          >
            <Mail className="w-4 h-4" />
            Connect Gmail
          </motion.button>
        )}
      </div>
    </motion.header>
  );
}
