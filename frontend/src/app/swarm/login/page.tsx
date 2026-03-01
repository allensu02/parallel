"use client";

import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Globe,
  ArrowLeft,
  Loader2,
  ShieldCheck,
  LogIn,
  ExternalLink,
  Mail,
  KeyRound,
  AlertTriangle,
} from "lucide-react";
import Header from "@/components/Header";
import {
  getSwarmAuthStatus,
  setupSwarmAuth,
  saveSwarmAuth,
  cancelSwarmAuth,
  clearSwarmAuth,
  loginSwarmAuth,
} from "@/lib/api";

export default function SwarmLoginPage() {
  const [authenticated, setAuthenticated] = useState(false);
  const [contextId, setContextId] = useState<string | null>(null);

  // Credentials form
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loggingIn, setLoggingIn] = useState(false);
  const [loginResult, setLoginResult] = useState<{ status: string; message: string; error?: string } | null>(null);

  // Clear saved login (expired session)
  const [clearing, setClearing] = useState(false);

  // Manual flow (fallback)
  const [manualLoading, setManualLoading] = useState(false);
  const [manualSaving, setManualSaving] = useState(false);
  const [liveViewUrl, setLiveViewUrl] = useState<string | null>(null);

  useEffect(() => {
    getSwarmAuthStatus()
      .then((s) => {
        setAuthenticated(s.authenticated);
        setContextId(s.context_id);
      })
      .catch(() => {});
  }, []);

  // Auto-close manual auth session on unmount
  useEffect(() => {
    if (!liveViewUrl) return;
    const cleanup = () => { cancelSwarmAuth().catch(() => {}); };
    window.addEventListener("beforeunload", cleanup);
    return () => {
      window.removeEventListener("beforeunload", cleanup);
    };
  }, [liveViewUrl]);

  const handleAutoLogin = async () => {
    if (!email || !password) return;
    setLoggingIn(true);
    setLoginResult(null);
    try {
      const result = await loginSwarmAuth(email, password);
      setLoginResult(result);
      if (result.status === "success") {
        setAuthenticated(true);
        setPassword(""); // clear password from memory
      }
    } catch (e: any) {
      setLoginResult({ status: "failed", message: e.message || "Login failed" });
    } finally {
      setLoggingIn(false);
    }
  };

  const handleManualSetup = async () => {
    setManualLoading(true);
    try {
      const result = await setupSwarmAuth();
      setLiveViewUrl(result.live_view_url);
      setContextId(result.context_id);
      window.open(result.live_view_url, "_blank");
    } catch (e) {
      console.error("Failed to setup auth:", e);
    } finally {
      setManualLoading(false);
    }
  };

  const handleManualSave = async () => {
    setManualSaving(true);
    try {
      await saveSwarmAuth();
      setAuthenticated(true);
      setLiveViewUrl(null);
    } catch (e) {
      console.error("Failed to save:", e);
    } finally {
      setManualSaving(false);
    }
  };

  const handleClearAuth = async () => {
    setClearing(true);
    try {
      await clearSwarmAuth();
      setAuthenticated(false);
      setContextId(null);
      setLoginResult(null);
    } catch (e) {
      console.error("Failed to clear:", e);
    } finally {
      setClearing(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col">
      <Header onAuthChange={() => {}} />

      <main className="flex-1 flex items-center justify-center px-6 py-12">
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          className="w-full max-w-md space-y-8"
        >
          {/* Back link */}
          <a
            href="/swarm"
            className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-primary transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            Back to Swarm
          </a>

          {/* Header */}
          <div className="text-center space-y-2">
            <div className="w-14 h-14 rounded-2xl bg-primary/10 border border-primary/20 flex items-center justify-center mx-auto">
              <LogIn className="w-7 h-7 text-primary" />
            </div>
            <h1 className="text-xl font-bold text-foreground">Browser Login</h1>
            <p className="text-sm text-muted">
              Enter your credentials. The agent will log in and save the session for all future tasks.
            </p>
          </div>

          {/* Status */}
          {authenticated && (
            <div className="flex flex-col items-center gap-2">
              <div className="flex items-center justify-center gap-2 text-xs text-success">
                <ShieldCheck className="w-4 h-4" />
                <span>Login saved — agents will use your session</span>
              </div>
              <button
                type="button"
                onClick={handleClearAuth}
                disabled={clearing}
                className="text-xs text-muted hover:text-error transition-colors disabled:opacity-50"
              >
                {clearing ? "Clearing..." : "Clear saved login (e.g. session expired)"}
              </button>
            </div>
          )}

          {/* Credentials form */}
          <div className="space-y-4">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted">Email</label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted/50" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@gmail.com"
                  className="w-full pl-10 pr-3 py-2.5 rounded-xl border border-border bg-surface-2 text-sm text-foreground placeholder:text-muted/40 focus:outline-none focus:border-primary/50 transition-colors"
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-muted">Password</label>
              <div className="relative">
                <KeyRound className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted/50" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  onKeyDown={(e) => e.key === "Enter" && handleAutoLogin()}
                  className="w-full pl-10 pr-3 py-2.5 rounded-xl border border-border bg-surface-2 text-sm text-foreground placeholder:text-muted/40 focus:outline-none focus:border-primary/50 transition-colors"
                />
              </div>
            </div>

            <button
              onClick={handleAutoLogin}
              disabled={!email || !password || loggingIn}
              className="w-full flex items-center justify-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold
                bg-primary text-background
                hover:bg-primary-light
                disabled:opacity-40 disabled:cursor-not-allowed
                transition-all duration-200 shadow-lg shadow-primary/20"
            >
              {loggingIn ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Logging in...
                </>
              ) : (
                <>
                  <LogIn className="w-4 h-4" />
                  {authenticated ? "Re-login" : "Log In & Save"}
                </>
              )}
            </button>

            {/* Result message */}
            {loginResult && (
              <div
                className={`flex items-start gap-2 p-3 rounded-lg text-sm ${
                  loginResult.status === "success"
                    ? "bg-success/10 text-success border border-success/20"
                    : "bg-error/10 text-error border border-error/20"
                }`}
              >
                {loginResult.status === "success" ? (
                  <ShieldCheck className="w-4 h-4 flex-shrink-0 mt-0.5" />
                ) : (
                  <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                )}
                <span>{loginResult.message}</span>
              </div>
            )}
          </div>

          {/* Divider */}
          <div className="flex items-center gap-3">
            <div className="flex-1 h-px bg-border" />
            <span className="text-[10px] text-muted uppercase tracking-wide">or log in manually</span>
            <div className="flex-1 h-px bg-border" />
          </div>

          {/* Manual login fallback */}
          {liveViewUrl ? (
            <div className="space-y-3 text-center">
              <p className="text-xs text-muted">
                Browser opened in a new tab. Log in there, then click Save.
              </p>
              <div className="flex items-center justify-center gap-3">
                <button
                  onClick={() => window.open(liveViewUrl, "_blank")}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-muted hover:text-primary hover:border-primary/50 transition-colors"
                >
                  <ExternalLink className="w-3.5 h-3.5" />
                  Open Browser Tab
                </button>
                <button
                  onClick={async () => { await cancelSwarmAuth().catch(() => {}); setLiveViewUrl(null); }}
                  className="px-3 py-1.5 rounded-lg text-xs text-muted border border-border hover:border-error/50 hover:text-error transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleManualSave}
                  disabled={manualSaving}
                  className="flex items-center gap-1.5 px-4 py-1.5 rounded-lg text-xs font-semibold bg-success/20 text-success border border-success/30 hover:bg-success/30 disabled:opacity-50 transition-colors"
                >
                  {manualSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
                  Save Login
                </button>
              </div>
            </div>
          ) : (
            <div className="text-center">
              <button
                onClick={handleManualSetup}
                disabled={manualLoading}
                className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-medium border border-border text-muted hover:border-primary/50 hover:text-primary disabled:opacity-50 transition-colors"
              >
                {manualLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Globe className="w-3.5 h-3.5" />}
                {manualLoading ? "Opening..." : "Open browser manually"}
              </button>
            </div>
          )}

          {/* Context ID */}
          {contextId && (
            <p className="text-center text-[10px] text-muted/30 font-mono">
              Context: {contextId}
            </p>
          )}
        </motion.div>
      </main>
    </div>
  );
}
