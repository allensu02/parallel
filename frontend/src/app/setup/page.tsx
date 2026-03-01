"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Hexagon, Mail, Calendar, Sparkles, Check,
  ChevronRight, ChevronLeft, Loader2, Settings,
} from "lucide-react";
import { useRouter } from "next/navigation";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Preferences {
  tone: string;
  formality: number;
  greeting_style: string;
  signoff_style: string;
  avg_length: string;
  uses_emoji: boolean;
}

const defaultPrefs: Preferences = {
  tone: "professional",
  formality: 3,
  greeting_style: "Hi [name],",
  signoff_style: "Best,",
  avg_length: "medium",
  uses_emoji: false,
};

export default function SetupPage() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzed, setAnalyzed] = useState(false);
  const [styleProfile, setStyleProfile] = useState<Record<string, unknown> | null>(null);
  const [prefs, setPrefs] = useState<Preferences>(defaultPrefs);
  const [calendarConnected, setCalendarConnected] = useState(false);

  const steps = [
    { title: "Learn Your Style", icon: <Sparkles className="w-5 h-5" /> },
    { title: "Preferences", icon: <Settings className="w-5 h-5" /> },
    { title: "Calendar", icon: <Calendar className="w-5 h-5" /> },
    { title: "Ready", icon: <Check className="w-5 h-5" /> },
  ];

  const handleAnalyze = useCallback(async () => {
    setAnalyzing(true);
    try {
      const res = await fetch(`${API_BASE}/api/profile/analyze`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        setStyleProfile(data.style_profile);
        setAnalyzed(true);

        // Pre-fill preferences from analysis
        const sp = data.style_profile;
        if (sp) {
          setPrefs({
            tone: sp.tone || "professional",
            formality: sp.formality || 3,
            greeting_style: sp.greeting_style || "Hi [name],",
            signoff_style: sp.signoff_style || "Best,",
            avg_length: sp.avg_length || "medium",
            uses_emoji: sp.uses_emoji || false,
          });
        }
      }
    } catch (e) {
      console.error("Analysis failed:", e);
    } finally {
      setAnalyzing(false);
    }
  }, []);

  const handleSavePreferences = useCallback(async () => {
    try {
      await fetch(`${API_BASE}/api/profile/preferences`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(prefs),
      });
    } catch (e) {
      console.error("Failed to save preferences:", e);
    }
  }, [prefs]);

  const handleFinish = useCallback(async () => {
    await handleSavePreferences();
    router.push("/");
  }, [handleSavePreferences, router]);

  return (
    <div className="min-h-screen flex flex-col items-center justify-center px-6 py-12">
      {/* Logo */}
      <motion.div
        initial={{ opacity: 0, y: -20 }}
        animate={{ opacity: 1, y: 0 }}
        className="mb-8 text-center"
      >
        <div className="w-16 h-16 hex-badge bg-gradient-to-br from-primary to-honey mx-auto mb-4 flex items-center justify-center">
          <Hexagon className="w-7 h-7 text-background" strokeWidth={2.5} />
        </div>
        <h1 className="text-2xl font-bold text-golden">Set Up Your Hive</h1>
        <p className="text-sm text-muted mt-1">Let the workers learn how you communicate</p>
      </motion.div>

      {/* Step indicators */}
      <div className="flex items-center gap-3 mb-8">
        {steps.map((s, i) => (
          <div key={i} className="flex items-center">
            {i > 0 && (
              <div className={`w-8 h-px mx-1 ${i <= step ? "bg-primary" : "bg-border"}`} />
            )}
            <button
              onClick={() => i <= step && setStep(i)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                i === step
                  ? "bg-honey-glow text-primary border border-primary/30"
                  : i < step
                  ? "bg-success/10 text-success border border-success/20"
                  : "bg-surface-2 text-muted border border-border"
              }`}
            >
              {i < step ? <Check className="w-3 h-3" /> : s.icon}
              <span className="hidden sm:inline">{s.title}</span>
            </button>
          </div>
        ))}
      </div>

      {/* Step content */}
      <motion.div
        className="w-full max-w-lg"
        key={step}
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        exit={{ opacity: 0, x: -20 }}
        transition={{ duration: 0.2 }}
      >
        <div className="rounded-xl bg-surface border border-border p-6 hover-glow">
          {/* Step 0: Analyze sent emails */}
          {step === 0 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Sparkles className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-semibold">Let the Hive Learn Your Style</h2>
              </div>
              <p className="text-sm text-muted leading-relaxed">
                We&apos;ll analyze your recent sent emails to understand your writing style,
                tone, and preferences. This helps the worker bees draft replies that sound like you.
              </p>

              {!analyzed ? (
                <button
                  onClick={handleAnalyze}
                  disabled={analyzing}
                  className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey disabled:opacity-50 transition-all text-background font-semibold shadow-lg shadow-primary/20"
                >
                  {analyzing ? (
                    <>
                      <Loader2 className="w-4 h-4 animate-spin" />
                      Analyzing your emails...
                    </>
                  ) : (
                    <>
                      <Mail className="w-4 h-4" />
                      Analyze My Sent Emails
                    </>
                  )}
                </button>
              ) : (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 text-success text-sm">
                    <Check className="w-4 h-4" />
                    Analysis complete!
                  </div>
                  {styleProfile && (
                    <div className="grid grid-cols-2 gap-3 text-xs">
                      <div className="p-3 rounded-lg bg-surface-2 border border-border">
                        <div className="text-muted mb-1">Tone</div>
                        <div className="font-medium text-foreground">{String(styleProfile.tone || "professional")}</div>
                      </div>
                      <div className="p-3 rounded-lg bg-surface-2 border border-border">
                        <div className="text-muted mb-1">Length</div>
                        <div className="font-medium text-foreground">{String(styleProfile.avg_length || "medium")}</div>
                      </div>
                      <div className="p-3 rounded-lg bg-surface-2 border border-border">
                        <div className="text-muted mb-1">Greeting</div>
                        <div className="font-medium text-foreground">{String(styleProfile.greeting_style || "Hi,")}</div>
                      </div>
                      <div className="p-3 rounded-lg bg-surface-2 border border-border">
                        <div className="text-muted mb-1">Sign-off</div>
                        <div className="font-medium text-foreground">{String(styleProfile.signoff_style || "Best,")}</div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              <button
                onClick={() => setStep(1)}
                className="text-xs text-muted hover:text-primary transition-colors"
              >
                Skip this step →
              </button>
            </div>
          )}

          {/* Step 1: Manual preferences */}
          {step === 1 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Settings className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-semibold">Your Preferences</h2>
              </div>
              <p className="text-sm text-muted">
                Fine-tune how the worker bees draft your emails.
              </p>

              <div className="space-y-3">
                {/* Tone */}
                <div>
                  <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Tone</label>
                  <div className="flex gap-2 flex-wrap">
                    {["casual", "friendly", "professional", "formal"].map((t) => (
                      <button
                        key={t}
                        onClick={() => setPrefs({ ...prefs, tone: t })}
                        className={`px-3 py-1.5 text-xs rounded-lg border transition-all ${
                          prefs.tone === t
                            ? "bg-honey-glow border-primary/30 text-primary"
                            : "bg-surface-2 border-border text-muted hover:text-foreground"
                        }`}
                      >
                        {t.charAt(0).toUpperCase() + t.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Formality slider */}
                <div>
                  <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">
                    Formality ({prefs.formality}/5)
                  </label>
                  <input
                    type="range"
                    min={1}
                    max={5}
                    value={prefs.formality}
                    onChange={(e) => setPrefs({ ...prefs, formality: parseInt(e.target.value) })}
                    className="w-full accent-primary"
                  />
                  <div className="flex justify-between text-[10px] text-muted">
                    <span>Very Casual</span>
                    <span>Very Formal</span>
                  </div>
                </div>

                {/* Length */}
                <div>
                  <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Reply Length</label>
                  <div className="flex gap-2">
                    {["short", "medium", "long"].map((l) => (
                      <button
                        key={l}
                        onClick={() => setPrefs({ ...prefs, avg_length: l })}
                        className={`flex-1 px-3 py-1.5 text-xs rounded-lg border transition-all ${
                          prefs.avg_length === l
                            ? "bg-honey-glow border-primary/30 text-primary"
                            : "bg-surface-2 border-border text-muted hover:text-foreground"
                        }`}
                      >
                        {l.charAt(0).toUpperCase() + l.slice(1)}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Greeting */}
                <div>
                  <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Greeting Style</label>
                  <input
                    type="text"
                    value={prefs.greeting_style}
                    onChange={(e) => setPrefs({ ...prefs, greeting_style: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-lg bg-surface-2 border border-border focus:border-primary outline-none text-foreground"
                    placeholder="Hi [name],"
                  />
                </div>

                {/* Sign-off */}
                <div>
                  <label className="text-xs text-muted uppercase tracking-wide block mb-1.5">Sign-off</label>
                  <input
                    type="text"
                    value={prefs.signoff_style}
                    onChange={(e) => setPrefs({ ...prefs, signoff_style: e.target.value })}
                    className="w-full px-3 py-1.5 text-sm rounded-lg bg-surface-2 border border-border focus:border-primary outline-none text-foreground"
                    placeholder="Best,"
                  />
                </div>

                {/* Emoji toggle */}
                <div className="flex items-center justify-between">
                  <label className="text-xs text-muted uppercase tracking-wide">Use Emoji</label>
                  <button
                    onClick={() => setPrefs({ ...prefs, uses_emoji: !prefs.uses_emoji })}
                    className={`w-10 h-5 rounded-full transition-all ${
                      prefs.uses_emoji ? "bg-primary" : "bg-border"
                    }`}
                  >
                    <div className={`w-4 h-4 rounded-full bg-foreground transition-transform ${
                      prefs.uses_emoji ? "translate-x-5" : "translate-x-0.5"
                    }`} />
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Step 2: Calendar */}
          {step === 2 && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 mb-2">
                <Calendar className="w-5 h-5 text-primary" />
                <h2 className="text-lg font-semibold">Calendar Integration</h2>
              </div>
              <p className="text-sm text-muted leading-relaxed">
                Connect your calendar so the Hive can check your availability when scheduling
                meetings or responding to calendar-related emails.
              </p>

              {calendarConnected ? (
                <div className="flex items-center gap-2 text-success text-sm p-3 rounded-lg bg-success/10 border border-success/20">
                  <Check className="w-4 h-4" />
                  Calendar connected! The Hive can check your availability.
                </div>
              ) : (
                <button
                  onClick={() => setCalendarConnected(true)}
                  className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey transition-all text-background font-semibold shadow-lg shadow-primary/20"
                >
                  <Calendar className="w-4 h-4" />
                  Enable Calendar Access
                </button>
              )}

              <p className="text-[10px] text-muted">
                Calendar access was included in your Google sign-in. This just enables the
                feature within the Hive.
              </p>
            </div>
          )}

          {/* Step 3: Ready */}
          {step === 3 && (
            <div className="space-y-4 text-center">
              <div className="w-16 h-16 hex-badge bg-gradient-to-br from-success to-success/70 mx-auto flex items-center justify-center">
                <Check className="w-7 h-7 text-background" strokeWidth={2.5} />
              </div>
              <h2 className="text-lg font-semibold text-golden">Your Hive is Ready!</h2>
              <p className="text-sm text-muted leading-relaxed">
                The worker bees are trained and ready to help you manage your inbox.
                They&apos;ll draft replies that match your style and preferences.
              </p>

              <button
                onClick={handleFinish}
                className="w-full flex items-center justify-center gap-2 px-5 py-3 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey transition-all text-background font-semibold shadow-lg shadow-primary/20"
              >
                <Hexagon className="w-4 h-4" />
                Go to Dashboard
              </button>
            </div>
          )}
        </div>

        {/* Navigation */}
        <div className="flex items-center justify-between mt-4">
          <button
            onClick={() => setStep(Math.max(0, step - 1))}
            disabled={step === 0}
            className="flex items-center gap-1 text-sm text-muted hover:text-foreground disabled:opacity-30 transition-colors"
          >
            <ChevronLeft className="w-4 h-4" />
            Back
          </button>
          {step < 3 && (
            <button
              onClick={() => {
                if (step === 1) handleSavePreferences();
                setStep(step + 1);
              }}
              className="flex items-center gap-1 text-sm text-primary hover:text-honey transition-colors font-medium"
            >
              Next
              <ChevronRight className="w-4 h-4" />
            </button>
          )}
        </div>
      </motion.div>
    </div>
  );
}
