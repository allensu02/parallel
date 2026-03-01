"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Hexagon, Check, X, AlertCircle, Loader2, OctagonX,
  Send, Filter, ArrowUpDown, Eye, EyeOff,
  ChevronDown,
} from "lucide-react";
import Header from "@/components/Header";
import LiveDraftPanel from "@/components/LiveDraftPanel";
import StatsBar from "@/components/StatsBar";
import QuestionChat, { type ChatMessage } from "@/components/QuestionChat";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import {
  type Run,
  type Job,
  type TaskInput,
  type ThreadContent,
  getGlobalSSEUrl,
  getRun,
  listJobs,
  listRuns,
  cancelRun,
  createTaskRun,
} from "@/lib/api";

/* ─── Main dashboard ─── */

export default function Dashboard() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="w-6 h-6 animate-spin text-primary" />
      </div>
    }>
      <DashboardInner />
    </Suspense>
  );
}

function DashboardInner() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  // Track ALL active runs, not just one
  const [activeRuns, setActiveRuns] = useState<Run[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [draftTokens, setDraftTokens] = useState<Map<string, string>>(new Map());
  const [frameData, setFrameData] = useState<Map<string, string>>(new Map());
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const contentCacheRef = useRef<Record<string, ThreadContent>>({});
  const [authToast, setAuthToast] = useState<string | null>(null);
  const [taskInput, setTaskInput] = useState("");
  const [pipelineFilter, setPipelineFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("active"); // "all" | "active" | "completed" | "failed"
  const [sortMode, setSortMode] = useState<string>("priority"); // "priority" | "recent" | "alpha"
  const [showFilterBar, setShowFilterBar] = useState(false);
  const [launchingTasks, setLaunchingTasks] = useState(false);

  const searchParams = useSearchParams();
  const router = useRouter();

  // Handle ?auth_success=true / ?auth_error=... after Google OAuth redirect
  useEffect(() => {
    const success = searchParams.get("auth_success");
    const error = searchParams.get("auth_error");
    if (success === "true") {
      setAuthToast("Connected to Google successfully!");
      router.replace("/", { scroll: false });
      setTimeout(() => setAuthToast(null), 4000);
    } else if (error) {
      setAuthToast(`Sign-in failed: ${error}`);
      router.replace("/", { scroll: false });
      setTimeout(() => setAuthToast(null), 6000);
    }
  }, [searchParams, router]);

  // On mount, resume ALL active runs (not just the first one)
  useEffect(() => {
    listRuns()
      .then(async (runs) => {
        const active = runs.filter((r) => r.status === "running" || r.status === "queued");
        if (active.length > 0) {
          setActiveRuns(active);
          // Load jobs from all active runs
          const allJobs = await Promise.all(
            active.map((r) => listJobs(r.id).catch(() => [] as Job[]))
          );
          setJobs(allJobs.flat());
        }
      })
      .catch(() => {});
  }, []);

  const handleAuthChange = useCallback((isAuthenticated: boolean) => {
    setAuthenticated(isAuthenticated);
  }, []);

  const hasPendingJobs = jobs.some(
    (j) => j.status === "pending_approval" || j.status === "waiting_for_input" || j.status === "running" || j.status === "queued"
  );

  // Poll for updates while any run is active
  useEffect(() => {
    if (activeRuns.length === 0) return;
    const terminal = ["completed", "failed", "cancelled"];
    const allDone = activeRuns.every((r) => terminal.includes(r.status));
    if (allDone && !hasPendingJobs) return;

    const timer = setInterval(async () => {
      try {
        // Poll all active runs in parallel
        const updates = await Promise.all(
          activeRuns.map(async (run) => {
            const [updatedRun, updatedJobs] = await Promise.all([
              getRun(run.id),
              listJobs(run.id),
            ]);
            return { run: updatedRun, jobs: updatedJobs };
          })
        );

        // Merge updated runs
        setActiveRuns(updates.map((u) => u.run));
        // Merge all jobs, keyed by job ID to avoid duplicates
        const jobMap = new Map<string, Job>();
        for (const u of updates) {
          for (const j of u.jobs) {
            jobMap.set(j.id, j);
          }
        }
        // Keep any jobs from runs we're no longer polling (shouldn't happen, but safe)
        setJobs((prev) => {
          const merged = new Map<string, Job>();
          for (const j of prev) merged.set(j.id, j);
          for (const [id, j] of jobMap) merged.set(id, j);
          return Array.from(merged.values());
        });
      } catch {
        // ignore
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [activeRuns, hasPendingJobs]);

  // ── Global SSE — receives events from ALL runs ──
  const sseUrl = activeRuns.length > 0 ? getGlobalSSEUrl() : null;

  const handleSSE = useCallback(
    (msg: SSEMessage) => {
      const { event, data } = msg;
      const eventRunId = data.run_id as string | undefined;

      if (event.startsWith("run.") && eventRunId) {
        setActiveRuns((prev) =>
          prev.map((r) => (r.id === eventRunId ? { ...r, ...data } as Run : r))
        );
      }

      if (event === "job.frame") {
        const jobId = data.job_id as string;
        const frame = data.frame as string;
        setFrameData((prev) => {
          const next = new Map(prev);
          next.set(jobId, frame);
          return next;
        });
        return;
      }

      if (
        event.startsWith("job.") &&
        event !== "job.draft_token" &&
        event !== "job.question" &&
        event !== "job.screenshot" &&
        event !== "job.visual_step"
      ) {
        const jobId = data.job_id as string;
        setJobs((prev) => {
          const idx = prev.findIndex((j) => j.id === jobId);
          if (idx === -1) {
            return [...prev, { ...data, id: jobId } as unknown as Job];
          }
          const updated = [...prev];
          updated[idx] = { ...updated[idx], ...data } as Job;
          return updated;
        });
      }

      if (event === "job.draft_token") {
        const jobId = data.job_id as string;
        const token = data.token as string;
        setDraftTokens((prev) => {
          const next = new Map(prev);
          next.set(jobId, (next.get(jobId) || "") + token);
          return next;
        });
      }

      if (event === "job.question") {
        const questionMsg: ChatMessage = {
          id: (data.question_id as string) || `q-${Date.now()}`,
          type: "question",
          jobId: data.job_id as string,
          subject: (data.subject as string) || "",
          text: data.question as string,
          timestamp: new Date().toISOString(),
        };
        setChatMessages((prev) => [...prev, questionMsg]);
      }
    },
    []
  );

  useSSE(sseUrl, handleSSE);

  const handleJobUpdated = useCallback((jobId: string, updates: Partial<Job>) => {
    setJobs((prev) =>
      prev.map((j) => (j.id === jobId ? { ...j, ...updates } : j))
    );
  }, []);

  const handleQuestionAnswered = useCallback((jobId: string) => {
    setChatMessages((prev) => [
      ...prev,
      {
        id: `a-${Date.now()}`,
        type: "answer",
        jobId,
        subject: "",
        text: "(answered)",
        timestamp: new Date().toISOString(),
      },
    ]);
  }, []);

  const anyRunActive = activeRuns.some(
    (r) => !["completed", "failed", "cancelled"].includes(r.status)
  );
  const [cancelling, setCancelling] = useState(false);

  const handleCancelRun = useCallback(async () => {
    if (activeRuns.length === 0 || cancelling) return;
    setCancelling(true);
    try {
      // Cancel all active runs
      await Promise.all(
        activeRuns
          .filter((r) => !["completed", "failed", "cancelled"].includes(r.status))
          .map((r) => cancelRun(r.id))
      );
      setActiveRuns((prev) =>
        prev.map((r) => ({ ...r, status: "cancelled" as const }))
      );
      setJobs((prev) =>
        prev.map((j) =>
          ["queued", "running", "pending_approval"].includes(j.status)
            ? { ...j, status: "skipped", current_step: "done" }
            : j
        )
      );
    } catch (e) {
      console.error("Failed to cancel runs:", e);
    } finally {
      setCancelling(false);
    }
  }, [activeRuns, cancelling]);

  // Launch tasks — ADDS a new run alongside existing ones
  const handleLaunchTasks = useCallback(async () => {
    if (!taskInput.trim() || launchingTasks) return;
    setLaunchingTasks(true);
    try {
      const lines = taskInput.split("\n").map(l => l.trim()).filter(Boolean);
      const tasks: TaskInput[] = lines.map(line => ({ description: line }));
      const run = await createTaskRun(tasks);

      // ADD the new run alongside existing active runs (don't replace!)
      setActiveRuns((prev) => [...prev, run]);
      setTaskInput("");
      setPipelineFilter("all");

      // Fetch jobs for the new run with retry
      const fetchJobs = async (retries: number) => {
        try {
          const newJobs = await listJobs(run.id);
          if (newJobs.length > 0 || retries <= 0) {
            // Append new jobs (don't replace existing ones)
            setJobs((prev) => {
              const existing = new Map(prev.map((j) => [j.id, j]));
              for (const j of newJobs) existing.set(j.id, j);
              return Array.from(existing.values());
            });
          } else {
            setTimeout(() => fetchJobs(retries - 1), 500);
          }
        } catch {
          if (retries > 0) setTimeout(() => fetchJobs(retries - 1), 500);
        }
      };
      fetchJobs(10);
    } catch (e) {
      console.error("Failed to launch tasks:", e);
    } finally {
      setLaunchingTasks(false);
    }
  }, [taskInput, launchingTasks]);

  // Unique pipeline types in current jobs (for filter chips)
  const activePipelineTypes = useMemo(() => {
    const types = new Set<string>();
    for (const j of jobs) types.add(j.pipeline_type || "gmail");
    return Array.from(types);
  }, [jobs]);

  // Summary counts (from ALL jobs, not filtered)
  const counts = useMemo(() => {
    const c = { review: 0, working: 0, done: 0, failed: 0, total: jobs.length };
    for (const j of jobs) {
      if (j.status === "pending_approval") c.review++;
      else if (j.status === "completed" || j.status === "skipped") c.done++;
      else if (j.status === "failed") c.failed++;
      else c.working++;
    }
    return c;
  }, [jobs]);

  // Advanced filtered + sorted jobs
  const filteredJobs = useMemo(() => {
    let result = [...jobs];

    // Pipeline filter
    if (pipelineFilter !== "all") {
      result = result.filter(j => (j.pipeline_type || "gmail") === pipelineFilter);
    }

    // Status filter
    const ACTIVE_STATUSES = ["queued", "running", "pending_approval", "waiting_for_input"];
    const DONE_STATUSES = ["completed", "skipped"];
    if (statusFilter === "active") {
      result = result.filter(j => ACTIVE_STATUSES.includes(j.status));
    } else if (statusFilter === "completed") {
      result = result.filter(j => DONE_STATUSES.includes(j.status));
    } else if (statusFilter === "failed") {
      result = result.filter(j => j.status === "failed");
    }

    // Sorting
    if (sortMode === "priority") {
      const order: Record<string, number> = {
        pending_approval: 0, waiting_for_input: 0,
        running: 1, queued: 2,
        completed: 3, skipped: 4, failed: 5,
      };
      result.sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
    } else if (sortMode === "alpha") {
      result.sort((a, b) => {
        const la = (a.subject || a.task_instruction || "").toLowerCase();
        const lb = (b.subject || b.task_instruction || "").toLowerCase();
        return la.localeCompare(lb);
      });
    } else if (sortMode === "recent") {
      // Reverse of insertion order — newest jobs (higher index in original array) first
      result.reverse();
    }

    return result;
  }, [jobs, pipelineFilter, statusFilter, sortMode]);

  const taskCount = taskInput.split("\n").filter(l => l.trim()).length;

  // The latest/primary run (for StatsBar display)
  const latestRun = activeRuns.length > 0 ? activeRuns[activeRuns.length - 1] : null;
  const hasRuns = activeRuns.length > 0;

  return (
    <div className={`min-h-screen flex flex-col honeycomb-bg ${chatMessages.length > 0 ? "pb-24" : ""}`}>
      <Header onAuthChange={handleAuthChange} />

      {/* Auth toast */}
      <AnimatePresence>
        {authToast && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3 }}
            className={`mx-auto mt-3 px-4 py-2 rounded-lg text-sm font-medium border shadow-lg backdrop-blur-sm ${
              authToast.startsWith("Connected")
                ? "bg-success/15 text-success border-success/25"
                : "bg-error/15 text-error border-error/25"
            }`}
          >
            <div className="flex items-center gap-2">
              {authToast.startsWith("Connected") ? (
                <Check className="w-4 h-4" />
              ) : (
                <AlertCircle className="w-4 h-4" />
              )}
              {authToast}
              <button onClick={() => setAuthToast(null)} className="ml-2 opacity-60 hover:opacity-100">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-6 flex flex-col gap-6">
        {/* ─── Task input (always visible) ─── */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4, delay: 0.1 }}
          className="rounded-xl border-2 border-honey/20 bg-gradient-to-br from-surface via-surface to-surface-2/50 backdrop-blur-sm p-6 space-y-4 warm-glow relative overflow-hidden"
        >
          {/* Large decorative hex accent */}
          <div className="absolute -top-12 -right-12 w-48 h-48 opacity-[0.04] pointer-events-none">
            <Hexagon className="w-full h-full text-honey" strokeWidth={1} />
          </div>
          <div className="absolute -bottom-8 -left-8 w-32 h-32 opacity-[0.03] pointer-events-none rotate-30">
            <Hexagon className="w-full h-full text-honey" strokeWidth={1} />
          </div>

          <div className="flex items-center gap-3 relative z-10">
            <motion.div
              animate={{ rotate: [0, 5, -5, 0] }}
              transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
              className="w-8 h-8 hex-badge bg-gradient-to-br from-honey via-primary to-primary-dark shadow-lg shadow-honey/25"
            >
              <Send className="w-4 h-4 text-background" />
            </motion.div>
            <h3 className="text-base font-extrabold text-golden">Deploy Workers</h3>
          </div>
          <p className="text-xs text-foreground/50 leading-relaxed relative z-10">
            Describe tasks in plain language, one per line. The hive auto-routes each to the right pipeline.
          </p>
          <textarea
            value={taskInput}
            onChange={e => setTaskInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                e.preventDefault();
                handleLaunchTasks();
              }
            }}
            placeholder={`Reply to my unread emails\nCreate a Google Slides deck about Q4 results\nFill out the feedback form at forms.google.com/...`}
            rows={3}
            className="w-full bg-surface-2/50 border-2 border-honey/12 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-foreground/25 focus:outline-none focus:border-honey/40 focus:ring-2 focus:ring-honey/20 focus:bg-surface-2/70 resize-none transition-all relative z-10"
          />
          <div className="flex items-center justify-between relative z-10">
            <span className="text-[10px] text-honey/40 font-semibold">
              {taskCount} task{taskCount !== 1 ? "s" : ""}
              <span className="ml-2 opacity-60">⌘+Enter to launch</span>
            </span>
            <motion.button
              whileHover={{ scale: 1.05, boxShadow: "0 0 30px rgba(255,224,102,0.35)" }}
              whileTap={{ scale: 0.95 }}
              onClick={handleLaunchTasks}
              disabled={!taskInput.trim() || launchingTasks}
              className="flex items-center gap-2 px-6 py-2.5 rounded-xl text-sm font-extrabold
                bg-gradient-to-r from-primary via-honey to-primary-light text-background
                shadow-lg shadow-honey/30
                disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none
                transition-all duration-200"
            >
              {launchingTasks ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              {launchingTasks ? "Deploying..." : "Deploy Swarm"}
            </motion.button>
          </div>
        </motion.div>

        {/* ─── Not authenticated hint ─── */}
        {authenticated === false && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="text-center py-6"
          >
            <p className="text-xs text-primary/50">
              Connect your Google account via the header to enable Gmail and GSuite pipelines.
            </p>
          </motion.div>
        )}

        {/* ─── Active swarm view (shown when there are any runs) ─── */}
        {hasRuns && (
          <>
            {/* Stats bar for the latest run */}
            {latestRun && <StatsBar run={latestRun} />}

            {/* Swarm header + filters */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center gap-3">
                <motion.div
                  animate={counts.working > 0 ? { rotate: [0, 5, -5, 0] } : {}}
                  transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                >
                  <Hexagon className="w-5 h-5 text-honey" />
                </motion.div>
                <h2 className="text-sm font-bold text-golden uppercase tracking-wider">
                  Swarm Activity
                </h2>
                {activeRuns.length > 1 && (
                  <span className="text-[10px] font-bold bg-honey-glow text-honey px-2 py-0.5 rounded-full border border-honey/20">
                    {activeRuns.length} swarms
                  </span>
                )}
                {counts.working > 0 && (
                  <motion.span
                    animate={{ opacity: [1, 0.7, 1] }}
                    transition={{ duration: 1.5, repeat: Infinity }}
                    className="text-[10px] font-bold bg-honey-glow text-honey px-2 py-0.5 rounded-full border border-honey/25"
                  >
                    {counts.working} buzzing
                  </motion.span>
                )}
                {counts.done > 0 && (
                  <span className="text-[10px] font-bold bg-success/15 text-success px-2 py-0.5 rounded-full border border-success/20">
                    {counts.done} done
                  </span>
                )}
                {counts.failed > 0 && (
                  <span className="text-[10px] font-bold bg-error/15 text-error px-2 py-0.5 rounded-full border border-error/20">
                    {counts.failed} failed
                  </span>
                )}
                <div className="flex-1" />

                {/* Filter toggle */}
                <button
                  onClick={() => setShowFilterBar(prev => !prev)}
                  className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-all duration-200 border ${
                    showFilterBar
                      ? "bg-honey-glow text-honey border-honey/30"
                      : "bg-surface-2/80 text-muted border-primary/15 hover:border-honey/30 hover:text-honey"
                  }`}
                >
                  <Filter className="w-3.5 h-3.5" />
                  Filters
                  {(statusFilter !== "active" || pipelineFilter !== "all" || sortMode !== "priority") && (
                    <span className="w-1.5 h-1.5 rounded-full bg-honey animate-pulse" />
                  )}
                  <ChevronDown className={`w-3 h-3 transition-transform duration-200 ${showFilterBar ? "rotate-180" : ""}`} />
                </button>

                {anyRunActive && (
                  <motion.button
                    initial={{ opacity: 0, scale: 0.9 }}
                    animate={{ opacity: 1, scale: 1 }}
                    onClick={handleCancelRun}
                    disabled={cancelling}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold
                      bg-error/10 text-error border border-error/25
                      hover:bg-error/20 hover:border-error/40
                      disabled:opacity-50 disabled:cursor-not-allowed
                      transition-all duration-150"
                  >
                    {cancelling ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <OctagonX className="w-3.5 h-3.5" />
                    )}
                    {cancelling ? "Stopping..." : "Kill Swarm"}
                  </motion.button>
                )}
              </div>

              {/* Advanced filter bar */}
              <AnimatePresence>
                {showFilterBar && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="overflow-hidden"
                  >
                    <div className="rounded-lg border border-primary/15 bg-surface/80 backdrop-blur-sm p-3 space-y-2.5">
                      {/* Row 1: Status filter */}
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[10px] text-primary/50 uppercase font-bold w-14 shrink-0">Status</span>
                        {[
                          { key: "active", label: "In Progress", icon: <Loader2 className="w-3 h-3" />, count: counts.working },
                          { key: "all", label: "All", icon: <Eye className="w-3 h-3" />, count: counts.total },
                          { key: "completed", label: "Completed", icon: <Check className="w-3 h-3" />, count: counts.done },
                          { key: "failed", label: "Failed", icon: <AlertCircle className="w-3 h-3" />, count: counts.failed },
                        ].map(opt => (
                          <button
                            key={opt.key}
                            onClick={() => setStatusFilter(opt.key)}
                            className={`flex items-center gap-1 text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all duration-200 ${
                              statusFilter === opt.key
                                ? "bg-honey-glow text-honey border-honey/30 shadow-sm shadow-honey/10"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:border-honey/25 hover:text-honey/80"
                            }`}
                          >
                            {opt.icon}
                            {opt.label}
                            <span className="opacity-50">({opt.count})</span>
                          </button>
                        ))}
                      </div>

                      {/* Row 2: Pipeline type filter */}
                      {activePipelineTypes.length > 1 && (
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[10px] text-primary/50 uppercase font-bold w-14 shrink-0">Type</span>
                          <button
                            onClick={() => setPipelineFilter("all")}
                            className={`text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all duration-200 ${
                              pipelineFilter === "all"
                                ? "bg-honey-glow text-honey border-honey/30 shadow-sm shadow-honey/10"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:border-honey/25 hover:text-honey/80"
                            }`}
                          >
                            All
                          </button>
                          {activePipelineTypes.map(pt => {
                            const count = jobs.filter(j => (j.pipeline_type || "gmail") === pt).length;
                            return (
                              <button
                                key={pt}
                                onClick={() => setPipelineFilter(pt)}
                                className={`text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all duration-200 capitalize ${
                                  pipelineFilter === pt
                                    ? "bg-honey-glow text-honey border-honey/30 shadow-sm shadow-honey/10"
                                    : "bg-surface-2/60 text-muted border-primary/10 hover:border-honey/25 hover:text-honey/80"
                                }`}
                              >
                                {pt} ({count})
                              </button>
                            );
                          })}
                        </div>
                      )}

                      {/* Row 3: Sort */}
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-[10px] text-primary/50 uppercase font-bold w-14 shrink-0">Sort</span>
                        {[
                          { key: "priority", label: "Priority" },
                          { key: "recent", label: "Most Recent" },
                          { key: "alpha", label: "A \u2192 Z" },
                        ].map(opt => (
                          <button
                            key={opt.key}
                            onClick={() => setSortMode(opt.key)}
                            className={`flex items-center gap-1 text-[10px] font-bold px-2.5 py-1 rounded-full border transition-all duration-200 ${
                              sortMode === opt.key
                                ? "bg-honey-glow text-honey border-honey/30 shadow-sm shadow-honey/10"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:border-honey/25 hover:text-honey/80"
                            }`}
                          >
                            <ArrowUpDown className="w-3 h-3" />
                            {opt.label}
                          </button>
                        ))}
                      </div>

                      {/* Reset */}
                      {(statusFilter !== "active" || pipelineFilter !== "all" || sortMode !== "priority") && (
                        <button
                          onClick={() => { setStatusFilter("active"); setPipelineFilter("all"); setSortMode("priority"); }}
                          className="text-[10px] text-muted hover:text-honey transition-colors"
                        >
                          Reset to defaults
                        </button>
                      )}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Honeycomb beehive grid */}
            {filteredJobs.length > 0 ? (
              <LiveDraftPanel
                jobs={filteredJobs}
                draftTokens={draftTokens}
                frameData={frameData}
                contentCache={contentCacheRef.current}
                onJobUpdated={handleJobUpdated}
              />
            ) : jobs.length > 0 ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="text-center py-16"
              >
                <EyeOff className="w-6 h-6 text-honey/40 mx-auto mb-3" />
                <p className="text-sm text-honey/50 font-medium">No bees match current filters</p>
                <button
                  onClick={() => { setStatusFilter("all"); setPipelineFilter("all"); }}
                  className="mt-2 text-xs text-honey font-semibold hover:underline"
                >
                  Show all workers
                </button>
              </motion.div>
            ) : (
              <motion.div
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                className="text-center py-16"
              >
                <motion.div
                  animate={{ rotate: [0, 360], scale: [1, 1.1, 1] }}
                  transition={{ rotate: { duration: 3, repeat: Infinity, ease: "linear" }, scale: { duration: 1.5, repeat: Infinity, ease: "easeInOut" } }}
                  className="w-14 h-14 hex-badge bg-gradient-to-br from-honey to-primary mx-auto mb-4 flex items-center justify-center shadow-xl shadow-honey/30"
                >
                  <Hexagon className="w-7 h-7 text-background" />
                </motion.div>
                <p className="text-base text-honey font-bold">Deploying worker bees...</p>
                <p className="text-xs text-honey/40 mt-1 font-medium">The hive is warming up</p>
                {/* Little buzzing dots */}
                <div className="flex items-center justify-center gap-1 mt-3">
                  {[0, 1, 2].map(i => (
                    <motion.div
                      key={i}
                      animate={{ y: [0, -6, 0] }}
                      transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }}
                      className="w-2 h-2 rounded-full bg-honey/60"
                    />
                  ))}
                </div>
              </motion.div>
            )}
          </>
        )}

        {/* Empty state — no active runs */}
        {!hasRuns && (
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3, duration: 0.6, type: "spring", stiffness: 80 }}
            className="text-center py-20 relative"
          >
            {/* Lots of floating particles */}
            <div className="absolute inset-0 pointer-events-none overflow-hidden">
              {Array.from({ length: 12 }).map((_, i) => (
                <div
                  key={i}
                  className="honey-particle"
                  style={{
                    left: `${10 + i * 7}%`,
                    bottom: "20%",
                    animationDelay: `${i * 0.5}s`,
                    opacity: 0,
                  }}
                />
              ))}
            </div>

            <motion.div
              animate={{ y: [0, -10, 0], rotate: [0, 3, -3, 0] }}
              transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
              className="w-24 h-24 hex-badge bg-gradient-to-br from-honey via-primary to-honey mx-auto mb-6 flex items-center justify-center relative shadow-2xl shadow-honey/30"
            >
              <Hexagon className="w-12 h-12 text-background" strokeWidth={1.5} />
              <div className="absolute inset-0 hex-badge animate-honey-glow" />
            </motion.div>
            <h2 className="text-3xl font-extrabold mb-3 text-golden">Welcome to Hive</h2>
            <p className="text-sm text-foreground/50 max-w-md mx-auto leading-relaxed">
              Describe what you need done above and deploy a swarm of AI workers.
              They&apos;ll handle Gmail, Google Docs, Slides, Sheets, and more — each task gets its own worker bee.
            </p>
            <div className="flex items-center justify-center gap-5 mt-8">
              {["Gmail", "Docs", "Slides", "Sheets", "Drive"].map((svc) => (
                <motion.span
                  key={svc}
                  whileHover={{ scale: 1.1, y: -2 }}
                  className="flex items-center gap-1.5 text-[11px] text-honey/50 font-semibold px-2.5 py-1 rounded-full bg-honey/5 border border-honey/10 hover:border-honey/25 hover:text-honey/80 transition-all cursor-default"
                >
                  <Hexagon className="w-3 h-3" /> {svc}
                </motion.span>
              ))}
            </div>
          </motion.div>
        )}
      </main>

      {/* Question chat */}
      <QuestionChat
        messages={chatMessages}
        runId={latestRun?.id || null}
        jobs={jobs}
        onAnswered={handleQuestionAnswered}
      />
    </div>
  );
}
