"use client";

import { useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Hexagon,
  Video,
  Square,
  Loader2,
  Check,
  X,
  ExternalLink,
} from "lucide-react";
import {
  type TaskDemo,
  startDemoRecording,
  stopDemoRecording,
} from "@/lib/api";

interface DemoRecorderProps {
  onDemoSaved?: (demo: TaskDemo) => void;
  onClose?: () => void;
}

type RecorderState = "idle" | "starting" | "recording" | "stopping" | "done";

export default function DemoRecorder({ onDemoSaved, onClose }: DemoRecorderProps) {
  const [state, setState] = useState<RecorderState>("idle");
  const [sessionId, setSessionId] = useState("");
  const [liveViewUrl, setLiveViewUrl] = useState("");
  const [demoName, setDemoName] = useState("");
  const [savedDemo, setSavedDemo] = useState<TaskDemo | null>(null);
  const [error, setError] = useState("");

  const handleStart = useCallback(async () => {
    setState("starting");
    setError("");
    try {
      const result = await startDemoRecording();
      setSessionId(result.session_id);
      setLiveViewUrl(result.live_view_url);
      setState("recording");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to start recording");
      setState("idle");
    }
  }, []);

  const handleStop = useCallback(async () => {
    if (!sessionId) return;
    setState("stopping");
    setError("");
    try {
      const demo = await stopDemoRecording(sessionId, demoName || "Untitled Demo");
      setSavedDemo(demo);
      setState("done");
      onDemoSaved?.(demo);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to stop recording");
      setState("recording");
    }
  }, [sessionId, demoName, onDemoSaved]);

  const handleClose = useCallback(() => {
    setState("idle");
    setSessionId("");
    setLiveViewUrl("");
    setDemoName("");
    setSavedDemo(null);
    setError("");
    onClose?.();
  }, [onClose]);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ type: "spring", stiffness: 300, damping: 25 }}
      className="relative rounded-2xl border-2 border-honey/25 bg-gradient-to-br from-surface via-surface to-surface-2/50 shadow-2xl shadow-black/40 overflow-hidden"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-honey/15 bg-surface-2/60">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-honey-glow flex items-center justify-center">
            <Video className="w-3.5 h-3.5 text-honey" />
          </div>
          <span className="font-bold text-sm text-foreground">
            {state === "done" ? "Demo Saved" : "Record a Demo"}
          </span>
          {state === "recording" && (
            <span className="flex items-center gap-1.5 ml-2 px-2 py-0.5 rounded-full bg-red-500/20 border border-red-500/30 text-red-400 text-xs font-semibold">
              <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse" />
              RECORDING
            </span>
          )}
        </div>
        <button
          onClick={handleClose}
          className="p-1 rounded-md hover:bg-surface-2 text-muted hover:text-foreground transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Body */}
      <div className="p-5">
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-sm">
            {error}
          </div>
        )}

        {/* Idle — show start button */}
        {state === "idle" && (
          <div className="text-center space-y-4 py-4">
            <div className="w-16 h-16 mx-auto rounded-full bg-honey-glow flex items-center justify-center">
              <Hexagon className="w-8 h-8 text-honey" />
            </div>
            <div>
              <p className="text-foreground font-semibold">Teach the Hive</p>
              <p className="text-muted text-sm mt-1">
                Record a browser workflow to create a reusable procedure for your agents.
              </p>
            </div>
            <button
              onClick={handleStart}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-lg bg-primary text-background font-semibold text-sm hover:brightness-110 transition-all shadow-lg shadow-honey/20"
            >
              <Video className="w-4 h-4" />
              Start Recording
            </button>
          </div>
        )}

        {/* Starting — spinner */}
        {state === "starting" && (
          <div className="text-center space-y-3 py-8">
            <Loader2 className="w-8 h-8 mx-auto text-honey animate-spin" />
            <p className="text-muted text-sm">Creating browser session...</p>
          </div>
        )}

        {/* Recording — iframe + controls */}
        {state === "recording" && (
          <div className="space-y-4">
            {/* Live view iframe */}
            <div className="relative w-full aspect-video rounded-lg overflow-hidden border border-honey/20 bg-black">
              {liveViewUrl ? (
                <iframe
                  src={liveViewUrl}
                  className="absolute inset-0 w-full h-full"
                  allow="clipboard-read; clipboard-write"
                />
              ) : (
                <div className="absolute inset-0 flex items-center justify-center text-muted text-sm">
                  Loading live view...
                </div>
              )}
            </div>

            {/* Open in new tab link */}
            {liveViewUrl && (
              <a
                href={liveViewUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-xs text-honey/70 hover:text-honey transition-colors"
              >
                <ExternalLink className="w-3 h-3" />
                Open in new tab
              </a>
            )}

            {/* Name input + stop */}
            <div className="flex items-end gap-3">
              <div className="flex-1">
                <label className="block text-xs text-muted mb-1">Demo name</label>
                <input
                  type="text"
                  value={demoName}
                  onChange={(e) => setDemoName(e.target.value)}
                  placeholder="e.g. 'Log into Canvas and check grades'"
                  className="w-full px-3 py-2 rounded-lg bg-surface-2/60 border border-primary/15 text-sm text-foreground placeholder:text-muted/50 focus:border-honey/50 focus:ring-2 focus:ring-honey/15 outline-none transition-all"
                />
              </div>
              <button
                onClick={handleStop}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-red-500/80 text-white font-semibold text-sm hover:bg-red-500 transition-all shadow-md"
              >
                <Square className="w-3.5 h-3.5" />
                Stop
              </button>
            </div>
          </div>
        )}

        {/* Stopping — spinner */}
        {state === "stopping" && (
          <div className="text-center space-y-3 py-8">
            <Loader2 className="w-8 h-8 mx-auto text-honey animate-spin" />
            <p className="text-muted text-sm">Processing recording &amp; synthesizing procedure...</p>
          </div>
        )}

        {/* Done — show result */}
        {state === "done" && savedDemo && (
          <div className="space-y-4 py-2">
            <div className="flex items-center gap-2 text-success">
              <Check className="w-5 h-5" />
              <span className="font-semibold text-sm">Demo saved successfully!</span>
            </div>
            <div className="p-3 rounded-lg bg-surface-2/50 border border-honey/10 space-y-2">
              <p className="text-xs text-muted">Name</p>
              <p className="text-sm text-foreground font-medium">{savedDemo.name}</p>
              <p className="text-xs text-muted mt-2">Synthesized Procedure</p>
              <p className="text-sm text-foreground/80 whitespace-pre-wrap leading-relaxed">
                {savedDemo.instruction_summary}
              </p>
            </div>
            <button
              onClick={handleClose}
              className="w-full py-2 rounded-lg bg-surface-2/80 border border-honey/15 text-sm text-foreground font-medium hover:bg-surface-2 transition-colors"
            >
              Close
            </button>
          </div>
        )}
      </div>
    </motion.div>
  );
}
