"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { Cpu, LogOut, Mail } from "lucide-react";
import { getAuthStatus, getAuthLoginUrl, logout, type AuthStatus } from "@/lib/api";

export default function Header() {
  const [auth, setAuth] = useState<AuthStatus | null>(null);

  useEffect(() => {
    getAuthStatus().then(setAuth).catch(() => setAuth({ authenticated: false, email: null }));
  }, []);

  const handleLogin = async () => {
    try {
      const { auth_url } = await getAuthLoginUrl();
      window.location.href = auth_url;
    } catch (err) {
      alert("Failed to start OAuth flow. Check that GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set in backend/.env");
    }
  };

  const handleLogout = async () => {
    await logout();
    setAuth({ authenticated: false, email: null });
  };

  return (
    <motion.header
      initial={{ y: -20, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.4, ease: "easeOut" }}
      className="flex items-center justify-between px-6 py-4 border-b border-border bg-surface"
    >
      <div className="flex items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
          <Cpu className="w-4 h-4 text-white" />
        </div>
        <h1 className="text-lg font-semibold tracking-tight">Parallel</h1>
        <span className="text-xs text-muted font-mono px-2 py-0.5 rounded bg-surface-2">
          agent framework
        </span>
      </div>

      <div className="flex items-center gap-3">
        {auth?.authenticated ? (
          <>
            <div className="flex items-center gap-2 text-sm text-muted">
              <Mail className="w-3.5 h-3.5" />
              <span>{auth.email}</span>
            </div>
            <button
              onClick={handleLogout}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md bg-surface-2 hover:bg-border transition-colors text-muted hover:text-foreground"
            >
              <LogOut className="w-3 h-3" />
              Sign out
            </button>
          </>
        ) : (
          <button
            onClick={handleLogin}
            className="flex items-center gap-2 px-4 py-2 text-sm rounded-lg bg-primary hover:bg-primary-light transition-colors text-white font-medium"
          >
            <Mail className="w-4 h-4" />
            Connect Gmail
          </button>
        )}
      </div>
    </motion.header>
  );
}
