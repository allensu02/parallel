"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Check, X, AlertCircle, Loader2, OctagonX,
  Send, Filter, EyeOff,
  ChevronDown, Video, Trash2,
} from "lucide-react";
import Header from "@/components/Header";
import LiveDraftPanel from "@/components/LiveDraftPanel";
import StatsBar from "@/components/StatsBar";
import QuestionChat, { type ChatMessage } from "@/components/QuestionChat";
import DemoRecorder from "@/components/DemoRecorder";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import {
  type Run,
  type Job,
  type TaskInput,
  type TaskDemo,
  type ThreadContent,
  getGlobalSSEUrl,
  getRun,
  listJobs,
  listRuns,
  cancelRun,
  createTaskRun,
  listDemos,
  deleteDemo,
} from "@/lib/api";

/* ─── Expandable demo card ─── */

function DemoCard({ demo, onDelete }: { demo: TaskDemo; onDelete: (id: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="group">
      <div
        className="flex items-start gap-2 px-3 py-2 cursor-pointer hover:bg-honey/5 transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        <div className="w-5 h-5 rounded-full bg-honey-glow flex items-center justify-center shrink-0 mt-0.5">
          <Video className="w-2.5 h-2.5 text-honey" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-xs font-semibold text-foreground truncate">{demo.name}</p>
          {!expanded && (
            <p className="text-[10px] text-muted mt-0.5 line-clamp-1">{demo.instruction_summary}</p>
          )}
        </div>
        <ChevronDown
          className={`w-3 h-3 text-muted/50 shrink-0 mt-1 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
        />
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(demo.id); }}
          className="p-1 rounded-md text-muted/40 hover:text-error hover:bg-error/10 transition-colors opacity-0 group-hover:opacity-100 shrink-0"
          title="Delete demo"
        >
          <Trash2 className="w-3 h-3" />
        </button>
      </div>
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="px-3 pb-2.5 pl-10">
              <p className="text-[10px] leading-relaxed text-muted whitespace-pre-wrap">
                {demo.instruction_summary || "No description."}
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

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
  const [demos, setDemos] = useState<TaskDemo[]>([]);
  const [showDemoRecorder, setShowDemoRecorder] = useState(false);
  const [showDemoList, setShowDemoList] = useState(false);

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

  // Load demos on mount
  useEffect(() => {
    listDemos().then(setDemos).catch(() => {});
  }, []);

  const handleDemoSaved = useCallback((demo: TaskDemo) => {
    setDemos((prev) => [demo, ...prev]);
    setShowDemoRecorder(false);
  }, []);

  const handleDeleteDemo = useCallback(async (demoId: string) => {
    try {
      await deleteDemo(demoId);
      setDemos((prev) => prev.filter((d) => d.id !== demoId));
    } catch (e) {
      console.error("Failed to delete demo:", e);
    }
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
          liveViewUrl: (data.live_view_url as string) || undefined,
          questionType: (data.type as "login_required" | "general") || "general",
        };
        setChatMessages((prev) => [...prev, questionMsg]);
      }

      if (event === "job.artifacts") {
        const artifacts = (data.artifacts as Array<{ type: string; label: string; content: string }>) || [];
        if (artifacts.length > 0) {
          const taskInstruction = (data.task_instruction as string) || "Task completed";
          const reportMsg: ChatMessage = {
            id: `report-${data.job_id}-${Date.now()}`,
            type: "report",
            jobId: data.job_id as string,
            subject: taskInstruction,
            text: "",
            timestamp: new Date().toISOString(),
            artifacts: artifacts.map((a) => ({
              type: a.type as "link" | "text" | "screenshot" | "live_view",
              label: a.label,
              content: a.content,
            })),
          };
          setChatMessages((prev) => [...prev, reportMsg]);
        }
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

  // Jobs for the hex operations — applies pipeline filter but NOT status filter.
  // The LiveDraftPanel handles fly-away animations internally; removing completed
  // jobs from the list instantly would bypass the fly-out animation.
  const operationsJobs = useMemo(() => {
    let result = [...jobs];
    if (pipelineFilter !== "all") {
      result = result.filter(j => (j.pipeline_type || "gmail") === pipelineFilter);
    }
    return result;
  }, [jobs, pipelineFilter]);

  // Advanced filtered + sorted jobs (for stats, sidebar list, etc.)
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

  return (
    <div className="min-h-screen flex flex-col">
      <Header onAuthChange={handleAuthChange} />

      <main className="flex-1 px-4 pb-44 pt-4">
        <AnimatePresence>
          {authToast && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className={`mx-auto mb-4 max-w-3xl px-4 py-2 rounded-lg text-sm font-medium border ${
                authToast.startsWith("Connected")
                  ? "bg-success/15 text-success border-success/25"
                  : "bg-error/15 text-error border-error/25"
              }`}
            >
              <div className="flex items-center gap-2">
                {authToast.startsWith("Connected") ? <Check className="w-4 h-4" /> : <AlertCircle className="w-4 h-4" />}
                <span className="flex-1">{authToast}</span>
                <button onClick={() => setAuthToast(null)} className="opacity-70 hover:opacity-100">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

        <div className="grid grid-cols-1 xl:grid-cols-12 gap-4 min-h-[calc(100vh-140px)]">
          <section className="xl:col-span-3 space-y-4">
            <div className="glass-panel p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-extrabold text-golden">New Run</h3>
                <span className="text-[10px] text-muted font-mono">{taskCount} tasks</span>
              </div>
              <textarea
                value={taskInput}
                onChange={e => setTaskInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault();
                    handleLaunchTasks();
                  }
                }}
                placeholder={`Reply to my unread emails\nCreate a Slides deck about Q4\nFill out forms.google.com/...`}
                rows={11}
                className="w-full bg-surface-2/60 border border-border rounded-lg px-3 py-2.5 text-xs leading-relaxed text-foreground placeholder:text-muted/60 focus:outline-none focus:border-primary/60 focus:ring-1 focus:ring-primary/30 resize-none"
              />
              <div className="flex items-center justify-between">
                <span className="text-[10px] text-muted">⌘/Ctrl + Enter to run</span>
                <button
                  onClick={handleLaunchTasks}
                  disabled={!taskInput.trim() || launchingTasks}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-bold bg-gradient-to-r from-primary to-primary-light text-background disabled:opacity-40"
                >
                  {launchingTasks ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                  {launchingTasks ? "Starting..." : "Start Run"}
                </button>
              </div>
            </div>

            <div className="glass-panel p-4 space-y-3">
              <div className="flex items-center justify-between">
                <button
                  onClick={() => setShowDemoList((prev) => !prev)}
                  className="flex items-center gap-1.5 text-xs font-semibold text-muted hover:text-foreground transition-colors"
                >
                  <Video className="w-3.5 h-3.5" />
                  Workflows ({demos.length})
                  <ChevronDown className={`w-3 h-3 transition-transform ${showDemoList ? "rotate-180" : ""}`} />
                </button>
                <button
                  onClick={() => setShowDemoRecorder(true)}
                  className="px-2.5 py-1 text-[11px] font-semibold rounded-md bg-surface-2 border border-border hover:border-primary/40"
                >
                  Record
                </button>
              </div>
              <AnimatePresence>
                {showDemoRecorder && (
                  <DemoRecorder onDemoSaved={handleDemoSaved} onClose={() => setShowDemoRecorder(false)} />
                )}
              </AnimatePresence>
              <AnimatePresence>
                {showDemoList && demos.length > 0 && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="overflow-hidden border border-border rounded-lg divide-y divide-border/60"
                  >
                    {demos.map((demo) => (
                      <DemoCard key={demo.id} demo={demo} onDelete={handleDeleteDemo} />
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
              {showDemoList && demos.length === 0 && (
                <p className="text-[11px] text-muted text-center py-2">No saved workflows yet.</p>
              )}
            </div>

            <div className="glass-panel p-4 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-xs font-semibold text-foreground">Filters</h4>
                <button
                  onClick={() => setShowFilterBar(prev => !prev)}
                  className="text-[11px] px-2 py-1 rounded-md border border-border hover:border-primary/40 flex items-center gap-1"
                >
                  <Filter className="w-3 h-3" />
                  {showFilterBar ? "Hide" : "Show"}
                </button>
              </div>
              <AnimatePresence>
                {showFilterBar && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="space-y-3 overflow-hidden"
                  >
                    <div className="flex flex-wrap gap-1.5">
                      {[
                        { key: "active", label: "Active", count: counts.working },
                        { key: "all", label: "All", count: counts.total },
                        { key: "completed", label: "Done", count: counts.done },
                        { key: "failed", label: "Failed", count: counts.failed },
                      ].map(opt => (
                        <button
                          key={opt.key}
                          onClick={() => setStatusFilter(opt.key)}
                          className={`text-[10px] px-2 py-1 rounded-md border transition-all ${
                            statusFilter === opt.key ? "bg-primary/15 border-primary/35 text-primary-light" : "bg-surface-2/50 border-border text-muted"
                          }`}
                        >
                          {opt.label} ({opt.count})
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      <button
                        onClick={() => setPipelineFilter("all")}
                        className={`text-[10px] px-2 py-1 rounded-md border transition-all ${
                          pipelineFilter === "all" ? "bg-primary/15 border-primary/35 text-primary-light" : "bg-surface-2/50 border-border text-muted"
                        }`}
                      >
                        All Types
                      </button>
                      {activePipelineTypes.map((pt) => (
                        <button
                          key={pt}
                          onClick={() => setPipelineFilter(pt)}
                          className={`text-[10px] px-2 py-1 rounded-md border capitalize transition-all ${
                            pipelineFilter === pt ? "bg-primary/15 border-primary/35 text-primary-light" : "bg-surface-2/50 border-border text-muted"
                          }`}
                        >
                          {pt}
                        </button>
                      ))}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {[
                        { key: "priority", label: "Priority" },
                        { key: "recent", label: "Recent" },
                        { key: "alpha", label: "A-Z" },
                      ].map(opt => (
                        <button
                          key={opt.key}
                          onClick={() => setSortMode(opt.key)}
                          className={`text-[10px] px-2 py-1 rounded-md border transition-all ${
                            sortMode === opt.key ? "bg-primary/15 border-primary/35 text-primary-light" : "bg-surface-2/50 border-border text-muted"
                          }`}
                        >
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </section>

          <section className="xl:col-span-6 space-y-4 min-h-0">
            {latestRun ? (
              <StatsBar run={latestRun} />
            ) : (
              <div className="glass-panel p-4">
                <h2 className="text-lg font-bold text-golden">Welcome to Parallel</h2>
                <p className="text-sm text-muted mt-1">
                  Use the left panel to start a run. Live execution appears here.
                </p>
              </div>
            )}

            <div className="glass-panel p-3 h-[620px] flex flex-col">
              <div className="flex items-center justify-between pb-2 border-b border-border/60">
                <div>
                  <p className="text-xs uppercase tracking-wide text-muted">Live Workspace</p>
                  <h3 className="text-sm font-semibold text-foreground">Execution Canvas</h3>
                </div>
                {anyRunActive && (
                  <button
                    onClick={handleCancelRun}
                    disabled={cancelling}
                    className="flex items-center gap-1 px-2.5 py-1.5 rounded-md text-xs font-semibold bg-error/10 text-error border border-error/25"
                  >
                    {cancelling ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <OctagonX className="w-3.5 h-3.5" />}
                    {cancelling ? "Stopping..." : "Stop Active Runs"}
                  </button>
                )}
              </div>
              <div className="flex-1 pt-3 min-h-0">
                <LiveDraftPanel
                  jobs={operationsJobs}
                  draftTokens={draftTokens}
                  frameData={frameData}
                  contentCache={contentCacheRef.current}
                  onJobUpdated={handleJobUpdated}
                />
              </div>
            </div>
          </section>

          <section className="xl:col-span-3 min-h-0">
            <div className="glass-panel p-4 h-full flex flex-col">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-sm font-bold text-golden">Run Queue</h3>
                <span className="text-[11px] text-muted">{filteredJobs.length} visible</span>
              </div>
              {authenticated === false && (
                <p className="text-[11px] text-primary-light/80 mb-3">
                  Connect Google from the header to run Gmail workflows.
                </p>
              )}
              <div className="space-y-2 overflow-y-auto pr-1">
                {filteredJobs.slice(0, 50).map((job) => (
                  <div key={job.id} className="rounded-lg border border-border bg-surface-2/45 p-2.5">
                    <div className="flex items-start justify-between gap-2">
                      <p className="text-xs font-semibold line-clamp-1">
                        {job.subject || job.task_instruction || job.thread_id.slice(0, 16)}
                      </p>
                      <span className={`text-[10px] px-1.5 py-0.5 rounded-md border ${
                        job.status === "running" ? "text-primary-light border-primary/30 bg-primary/10" :
                        job.status === "completed" ? "text-success border-success/30 bg-success/10" :
                        job.status === "failed" ? "text-error border-error/30 bg-error/10" :
                        "text-muted border-border bg-surface"
                      }`}>
                        {job.status}
                      </span>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 text-[10px] text-muted">
                      <span>{job.pipeline_type || "gmail"}</span>
                      {job.confidence != null && <span>{(job.confidence * 100).toFixed(0)}%</span>}
                      {job.tokens_used > 0 && <span>{job.tokens_used} tok</span>}
                    </div>
                    {job.error_msg && (
                      <p className="mt-1 text-[10px] text-error line-clamp-1">{job.error_msg}</p>
                    )}
                  </div>
                ))}
                {filteredJobs.length === 0 && (
                  <div className="text-center py-6">
                    <EyeOff className="w-4 h-4 text-muted mx-auto mb-1" />
                    <p className="text-[11px] text-muted">No tasks match current filters.</p>
                    <button
                      onClick={() => { setStatusFilter("all"); setPipelineFilter("all"); }}
                      className="text-[11px] text-primary-light mt-1"
                    >
                      Reset filters
                    </button>
                  </div>
                )}
              </div>
            </div>
          </section>
        </div>
      </main>

      <QuestionChat
        messages={chatMessages}
        runId={latestRun?.id || null}
        jobs={jobs}
        onAnswered={handleQuestionAnswered}
      />
    </div>
  );
}
