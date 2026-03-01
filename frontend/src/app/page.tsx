"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Hexagon, Check, X, AlertCircle, Loader2, OctagonX,
  Send, Filter, ArrowUpDown, Eye, EyeOff,
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

  // Jobs for the hex swarm — applies pipeline filter but NOT status filter.
  // The LiveDraftPanel handles fly-away animations internally; removing completed
  // jobs from the list instantly would bypass the fly-out animation.
  const swarmJobs = useMemo(() => {
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
  const hasRuns = activeRuns.length > 0;

  return (
    <div className="min-h-screen relative overflow-hidden">
      {/* ═══ SVG GOO FILTER ═══ */}
      <svg style={{ position: "absolute", width: 0, height: 0 }} aria-hidden="true">
        <defs>
          <filter id="goo">
            <feGaussianBlur in="SourceGraphic" stdDeviation="5" result="blur" />
            <feColorMatrix in="blur" mode="matrix"
              values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -7" result="goo" />
            <feComposite in="SourceGraphic" in2="goo" operator="atop" />
          </filter>
        </defs>
      </svg>

      {/* ═══ LAYER 0: Full-screen hex hive background ═══ */}
      <LiveDraftPanel
        jobs={swarmJobs}
        draftTokens={draftTokens}
        frameData={frameData}
        contentCache={contentCacheRef.current}
        onJobUpdated={handleJobUpdated}
        fullscreen
      />

      {/* ═══ LAYER 1: Floating glass UI — left sidebar + header ═══ */}
      <div className="relative z-10 pointer-events-none min-h-screen flex flex-col">
        {/* Header — spans full width */}
        <div className="pointer-events-auto">
          <Header onAuthChange={handleAuthChange} />
        </div>

        {/* Auth toast */}
        <AnimatePresence>
          {authToast && (
            <motion.div
              initial={{ opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.3 }}
              className={`pointer-events-auto mx-auto mt-3 px-4 py-2 rounded-lg text-sm font-medium border shadow-lg backdrop-blur-sm ${
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

        {/* Left sidebar — narrow panel, leaves right side open for swarm */}
        <aside className="w-[380px] max-h-[calc(100vh-80px)] overflow-y-auto px-4 py-4 flex flex-col gap-4 pointer-events-none">
          {/* ─── Task input ─── */}
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.4, delay: 0.1 }}
            className="pointer-events-auto glass-panel p-4 space-y-3 relative overflow-hidden honey-drip-edge"
          >
            <div className="flex items-center gap-2.5 relative z-10">
              <motion.div
                animate={{ rotate: [0, 5, -5, 0] }}
                transition={{ duration: 3, repeat: Infinity, ease: "easeInOut" }}
                className="w-7 h-7 hex-badge bg-gradient-to-br from-honey via-primary to-primary-dark shadow-lg shadow-honey/25"
              >
                <Send className="w-3.5 h-3.5 text-background" />
              </motion.div>
              <h3 className="text-sm font-extrabold text-golden">Deploy Workers</h3>
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
              rows={12}
              className="w-full bg-surface-2/50 border border-honey/12 rounded-lg px-3 py-2.5 text-xs leading-relaxed text-foreground placeholder:text-foreground/25 focus:outline-none focus:border-honey/40 focus:ring-1 focus:ring-honey/20 resize-none transition-all relative z-10"
            />
            <div className="flex items-center justify-between relative z-10">
              <span className="text-[9px] text-honey/40 font-semibold">
                {taskCount} task{taskCount !== 1 ? "s" : ""}
                <span className="ml-1.5 opacity-60">⌘+Enter</span>
              </span>
              <motion.button
                whileHover={{ scale: 1.05, boxShadow: "0 0 20px rgba(232,163,23,0.35)" }}
                whileTap={{ scale: 0.95 }}
                onClick={handleLaunchTasks}
                disabled={!taskInput.trim() || launchingTasks}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-extrabold
                  bg-gradient-to-r from-primary via-honey to-primary-light text-background
                  shadow-lg shadow-honey/30
                  disabled:opacity-40 disabled:cursor-not-allowed disabled:shadow-none
                  transition-all duration-200"
              >
                {launchingTasks ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Send className="w-3.5 h-3.5" />
                )}
                {launchingTasks ? "Deploying..." : "Deploy Swarm"}
              </motion.button>
            </div>
          </motion.div>

          {/* ─── Demos ─── */}
          <div className="space-y-2 pointer-events-auto">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setShowDemoList((prev) => !prev)}
                className="flex items-center gap-1.5 text-[10px] font-bold text-muted hover:text-honey transition-colors"
              >
                <Video className="w-3 h-3" />
                Demos
                {demos.length > 0 && (
                  <span className="px-1 py-0.5 rounded-full bg-honey-glow text-honey text-[9px] font-bold border border-honey/20">
                    {demos.length}
                  </span>
                )}
                <ChevronDown className={`w-2.5 h-2.5 transition-transform duration-200 ${showDemoList ? "rotate-180" : ""}`} />
              </button>
              <div className="flex-1" />
              <button
                onClick={() => setShowDemoRecorder(true)}
                className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold bg-surface-2/80 border border-honey/15 text-honey/80 hover:text-honey hover:border-honey/30 transition-all"
              >
                <Video className="w-3 h-3" />
                Record
              </button>
            </div>

            <AnimatePresence>
              {showDemoRecorder && (
                <DemoRecorder
                  onDemoSaved={handleDemoSaved}
                  onClose={() => setShowDemoRecorder(false)}
                />
              )}
            </AnimatePresence>

            <AnimatePresence>
              {showDemoList && demos.length > 0 && (
                <motion.div
                  initial={{ height: 0, opacity: 0 }}
                  animate={{ height: "auto", opacity: 1 }}
                  exit={{ height: 0, opacity: 0 }}
                  transition={{ duration: 0.15 }}
                  className="overflow-hidden"
                >
                  <div className="glass-panel divide-y divide-honey/8 overflow-hidden">
                    {demos.map((demo) => (
                      <DemoCard key={demo.id} demo={demo} onDelete={handleDeleteDemo} />
                    ))}
                  </div>
                </motion.div>
              )}
              {showDemoList && demos.length === 0 && (
                <motion.p
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  className="text-[10px] text-muted/50 text-center py-2"
                >
                  No demos yet.
                </motion.p>
              )}
            </AnimatePresence>
          </div>

          {/* ─── Not authenticated hint ─── */}
          {authenticated === false && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="pointer-events-auto text-center py-2"
            >
              <p className="text-[10px] text-primary/50">
                Connect Google via the header for Gmail and GSuite.
              </p>
            </motion.div>
          )}

          {/* ─── Active swarm controls ─── */}
          {hasRuns && (
            <div className="pointer-events-auto space-y-3">
              {latestRun && <StatsBar run={latestRun} />}

              {/* Swarm header + actions */}
              <div className="glass-panel p-3 space-y-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <motion.div
                    animate={counts.working > 0 ? { rotate: [0, 5, -5, 0] } : {}}
                    transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
                  >
                    <Hexagon className="w-4 h-4 text-honey" />
                  </motion.div>
                  <h2 className="text-[11px] font-bold text-golden uppercase tracking-wider">
                    Swarm
                  </h2>
                  {counts.working > 0 && (
                    <motion.span
                      animate={{ opacity: [1, 0.7, 1] }}
                      transition={{ duration: 1.5, repeat: Infinity }}
                      className="text-[9px] font-bold bg-honey-glow text-honey px-1.5 py-0.5 rounded-full border border-honey/25"
                    >
                      {counts.working} buzzing
                    </motion.span>
                  )}
                  {counts.done > 0 && (
                    <span className="text-[9px] font-bold bg-success/15 text-success px-1.5 py-0.5 rounded-full border border-success/20">
                      {counts.done} done
                    </span>
                  )}
                  <div className="flex-1" />
                  {anyRunActive && (
                    <button
                      onClick={handleCancelRun}
                      disabled={cancelling}
                      className="flex items-center gap-1 px-2 py-1 rounded-md text-[10px] font-semibold
                        bg-error/10 text-error border border-error/25
                        hover:bg-error/20 disabled:opacity-50 transition-all"
                    >
                      {cancelling ? <Loader2 className="w-3 h-3 animate-spin" /> : <OctagonX className="w-3 h-3" />}
                      {cancelling ? "..." : "Kill"}
                    </button>
                  )}
                </div>

                {/* Compact filter row */}
                <div className="flex items-center gap-1.5 flex-wrap">
                  <button
                    onClick={() => setShowFilterBar(prev => !prev)}
                    className={`flex items-center gap-1 px-2 py-1 rounded-md text-[9px] font-semibold transition-all border ${
                      showFilterBar
                        ? "bg-honey-glow text-honey border-honey/30"
                        : "bg-surface-2/80 text-muted border-primary/15 hover:text-honey"
                    }`}
                  >
                    <Filter className="w-3 h-3" />
                    Filters
                    <ChevronDown className={`w-2.5 h-2.5 transition-transform ${showFilterBar ? "rotate-180" : ""}`} />
                  </button>
                  {activeRuns.length > 1 && (
                    <span className="text-[9px] font-bold bg-honey-glow text-honey px-1.5 py-0.5 rounded-full border border-honey/20">
                      {activeRuns.length} swarms
                    </span>
                  )}
                </div>
              </div>

              {/* Expanded filters */}
              <AnimatePresence>
                {showFilterBar && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: 0.15 }}
                    className="overflow-hidden"
                  >
                    <div className="glass-panel p-3 space-y-2">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[9px] text-primary/50 uppercase font-bold w-10 shrink-0">Status</span>
                        {[
                          { key: "active", label: "Active", count: counts.working },
                          { key: "all", label: "All", count: counts.total },
                          { key: "completed", label: "Done", count: counts.done },
                          { key: "failed", label: "Failed", count: counts.failed },
                        ].map(opt => (
                          <button
                            key={opt.key}
                            onClick={() => setStatusFilter(opt.key)}
                            className={`text-[9px] font-bold px-2 py-0.5 rounded-full border transition-all ${
                              statusFilter === opt.key
                                ? "bg-honey-glow text-honey border-honey/30"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:text-honey/80"
                            }`}
                          >
                            {opt.label} ({opt.count})
                          </button>
                        ))}
                      </div>
                      {activePipelineTypes.length > 1 && (
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <span className="text-[9px] text-primary/50 uppercase font-bold w-10 shrink-0">Type</span>
                          <button
                            onClick={() => setPipelineFilter("all")}
                            className={`text-[9px] font-bold px-2 py-0.5 rounded-full border transition-all ${
                              pipelineFilter === "all"
                                ? "bg-honey-glow text-honey border-honey/30"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:text-honey/80"
                            }`}
                          >All</button>
                          {activePipelineTypes.map(pt => (
                            <button
                              key={pt}
                              onClick={() => setPipelineFilter(pt)}
                              className={`text-[9px] font-bold px-2 py-0.5 rounded-full border transition-all capitalize ${
                                pipelineFilter === pt
                                  ? "bg-honey-glow text-honey border-honey/30"
                                  : "bg-surface-2/60 text-muted border-primary/10 hover:text-honey/80"
                              }`}
                            >{pt}</button>
                          ))}
                        </div>
                      )}
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-[9px] text-primary/50 uppercase font-bold w-10 shrink-0">Sort</span>
                        {[
                          { key: "priority", label: "Priority" },
                          { key: "recent", label: "Recent" },
                          { key: "alpha", label: "A→Z" },
                        ].map(opt => (
                          <button
                            key={opt.key}
                            onClick={() => setSortMode(opt.key)}
                            className={`text-[9px] font-bold px-2 py-0.5 rounded-full border transition-all ${
                              sortMode === opt.key
                                ? "bg-honey-glow text-honey border-honey/30"
                                : "bg-surface-2/60 text-muted border-primary/10 hover:text-honey/80"
                            }`}
                          >{opt.label}</button>
                        ))}
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Empty states */}
              {filteredJobs.length === 0 && jobs.length > 0 && (
                <div className="glass-panel p-3 text-center">
                  <EyeOff className="w-4 h-4 text-honey/40 mx-auto mb-1" />
                  <p className="text-[10px] text-honey/50">No bees match filters</p>
                  <button
                    onClick={() => { setStatusFilter("all"); setPipelineFilter("all"); }}
                    className="text-[10px] text-honey font-semibold hover:underline"
                  >Show all</button>
                </div>
              )}

              {filteredJobs.length === 0 && jobs.length === 0 && (
                <div className="glass-panel p-4 text-center">
                  <motion.div
                    animate={{ rotate: [0, 360] }}
                    transition={{ duration: 3, repeat: Infinity, ease: "linear" }}
                    className="w-10 h-10 hex-badge bg-gradient-to-br from-honey to-primary mx-auto mb-2 flex items-center justify-center"
                  >
                    <Hexagon className="w-5 h-5 text-background" />
                  </motion.div>
                  <p className="text-xs text-honey font-bold">Deploying...</p>
                  <div className="flex items-center justify-center gap-1 mt-2">
                    {[0, 1, 2].map(i => (
                      <motion.div
                        key={i}
                        animate={{ y: [0, -4, 0] }}
                        transition={{ duration: 0.6, repeat: Infinity, delay: i * 0.15 }}
                        className="w-1.5 h-1.5 rounded-full bg-honey/60"
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Empty state — no active runs */}
          {!hasRuns && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.3, duration: 0.5 }}
              className="pointer-events-auto glass-panel p-5 text-center"
            >
              <motion.div
                animate={{ y: [0, -6, 0], rotate: [0, 3, -3, 0] }}
                transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
                className="w-16 h-16 hex-badge bg-gradient-to-br from-honey via-primary to-honey mx-auto mb-4 flex items-center justify-center relative shadow-xl shadow-honey/30"
              >
                <Hexagon className="w-8 h-8 text-background" strokeWidth={1.5} />
                <div className="absolute inset-0 hex-badge animate-honey-glow" />
              </motion.div>
              <h2 className="text-xl font-extrabold mb-2 text-golden">Welcome to Hive</h2>
              <p className="text-[11px] text-foreground/50 leading-relaxed">
                Deploy a swarm of AI workers to handle Gmail, Docs, Slides, and more.
              </p>
              <div className="flex items-center justify-center gap-2 mt-4 flex-wrap">
                {["Gmail", "Docs", "Slides", "Sheets"].map((svc) => (
                  <span
                    key={svc}
                    className="flex items-center gap-1 text-[9px] text-honey/50 font-semibold px-2 py-0.5 rounded-full bg-honey/5 border border-honey/10"
                  >
                    <Hexagon className="w-2.5 h-2.5" /> {svc}
                  </span>
                ))}
              </div>
            </motion.div>
          )}
        </aside>
      </div>

      {/* ═══ LAYER 2: QuestionChat fixed at bottom ═══ */}
      <QuestionChat
        messages={chatMessages}
        runId={latestRun?.id || null}
        jobs={jobs}
        onAnswered={handleQuestionAnswered}
      />
    </div>
  );
}
