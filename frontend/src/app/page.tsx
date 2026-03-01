"use client";

import { Suspense, useCallback, useEffect, useMemo, useState, useRef } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import {
  Hexagon, ArrowLeft, Check, X, AlertCircle, Clock, Loader2, OctagonX,
} from "lucide-react";
import Header from "@/components/Header";
import InboxSelector from "@/components/InboxSelector";
import LiveDraftPanel from "@/components/LiveDraftPanel";
import StatsBar from "@/components/StatsBar";
import QuestionChat, { type ChatMessage } from "@/components/QuestionChat";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import {
  type Run,
  type Job,
  type ThreadContent,
  getSSEUrl,
  getRun,
  listJobs,
  listRuns,
  cancelRun,
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
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [draftTokens, setDraftTokens] = useState<Map<string, string>>(new Map());
  const [frameData, setFrameData] = useState<Map<string, string>>(new Map());
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const contentCacheRef = useRef<Record<string, ThreadContent>>({});
  const [authToast, setAuthToast] = useState<string | null>(null);

  const searchParams = useSearchParams();
  const router = useRouter();

  // Handle ?auth_success=true / ?auth_error=... after Google OAuth redirect
  useEffect(() => {
    const success = searchParams.get("auth_success");
    const error = searchParams.get("auth_error");
    if (success === "true") {
      setAuthToast("Connected to Google successfully!");
      // Clear the query params from the URL
      router.replace("/", { scroll: false });
      // Auto-dismiss after 4 seconds
      setTimeout(() => setAuthToast(null), 4000);
    } else if (error) {
      setAuthToast(`Sign-in failed: ${error}`);
      router.replace("/", { scroll: false });
      setTimeout(() => setAuthToast(null), 6000);
    }
  }, [searchParams, router]);

  useEffect(() => {
    listRuns()
      .then(setRuns)
      .catch(() => {});
  }, []);

  const handleAuthChange = useCallback((isAuthenticated: boolean) => {
    setAuthenticated(isAuthenticated);
  }, []);

  const hasPendingJobs = jobs.some(
    (j) => j.status === "pending_approval" || j.status === "waiting_for_input" || j.status === "running" || j.status === "queued"
  );

  useEffect(() => {
    if (!activeRun) return;
    const terminal = ["completed", "failed", "cancelled"];
    const runDone = terminal.includes(activeRun.status);
    if (runDone && !hasPendingJobs) return;

    const timer = setInterval(async () => {
      try {
        const [updatedRun, updatedJobs] = await Promise.all([
          getRun(activeRun.id),
          listJobs(activeRun.id),
        ]);
        setActiveRun(updatedRun);
        setJobs(updatedJobs);
      } catch {
        // ignore
      }
    }, 1000);
    return () => clearInterval(timer);
  }, [activeRun, hasPendingJobs]);

  const sseUrl = activeRun ? getSSEUrl(activeRun.id) : null;

  const handleSSE = useCallback(
    (msg: SSEMessage) => {
      const { event, data } = msg;

      if (event.startsWith("run.")) {
        setActiveRun((prev) => {
          if (!prev) return prev;
          return { ...prev, ...data } as Run;
        });
      }

      if (event === "job.frame") {
        const jobId = data.job_id as string;
        const frame = data.frame as string;
        setFrameData((prev) => {
          const next = new Map(prev);
          next.set(jobId, frame);
          return next;
        });
        return; // Don't process frame events as job updates
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

  const handleRunCreated = useCallback(async (run: Run, cache: Record<string, ThreadContent>) => {
    setActiveRun(run);
    setJobs([]);
    setDraftTokens(new Map());
    setFrameData(new Map());
    setChatMessages([]);
    contentCacheRef.current = { ...contentCacheRef.current, ...cache };
    setRuns((prev) => [run, ...prev]);
    // Fetch jobs immediately, then retry quickly if empty (backend may still be creating them)
    const fetchJobs = async (retries: number) => {
      try {
        const j = await listJobs(run.id);
        if (j.length > 0 || retries <= 0) {
          setJobs(j);
        } else {
          setTimeout(() => fetchJobs(retries - 1), 300);
        }
      } catch {
        if (retries > 0) setTimeout(() => fetchJobs(retries - 1), 300);
      }
    };
    fetchJobs(5);
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

  const handleBackToInbox = () => {
    setActiveRun(null);
    setJobs([]);
    setDraftTokens(new Map());
    setFrameData(new Map());
    setChatMessages([]);
  };

  // Sort: pending_approval first, then running/drafting, then queued, then done
  const sortedJobs = useMemo(() => {
    return [...jobs].sort((a, b) => {
      const order: Record<string, number> = {
        pending_approval: 0,
        waiting_for_input: 0,
        running: 1,
        queued: 2,
        completed: 3,
        skipped: 4,
        failed: 5,
      };
      return (order[a.status] ?? 3) - (order[b.status] ?? 3);
    });
  }, [jobs]);

  const isRunActive = activeRun && !["completed", "failed", "cancelled"].includes(activeRun.status);
  const [cancelling, setCancelling] = useState(false);

  const handleCancelRun = useCallback(async () => {
    if (!activeRun || cancelling) return;
    setCancelling(true);
    try {
      await cancelRun(activeRun.id);
      setActiveRun((prev) => prev ? { ...prev, status: "cancelled" } : prev);
      // Mark all non-terminal jobs as skipped optimistically
      setJobs((prev) =>
        prev.map((j) =>
          ["queued", "running", "pending_approval"].includes(j.status)
            ? { ...j, status: "skipped", current_step: "done" }
            : j
        )
      );
    } catch (e) {
      console.error("Failed to cancel run:", e);
    } finally {
      setCancelling(false);
    }
  }, [activeRun, cancelling]);

  // Summary counts for the unified swarm view
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

  return (
    <div className={`min-h-screen flex flex-col ${chatMessages.length > 0 ? "pb-24" : ""}`}>
      <Header onAuthChange={handleAuthChange} />

      {/* Auth toast */}
      <AnimatePresence>
        {authToast && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.3 }}
            className={`mx-auto mt-3 px-4 py-2 rounded-lg text-sm font-medium border shadow-lg ${
              authToast.startsWith("Connected")
                ? "bg-success/10 text-success border-success/20"
                : "bg-error/10 text-error border-error/20"
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

      <main className="flex-1 max-w-5xl mx-auto w-full px-6 py-6 space-y-6">
        {/* Active run view — unified swarm */}
        {activeRun ? (
          <>
            <div className="flex items-center gap-4">
              <button
                onClick={handleBackToInbox}
                className="flex items-center gap-1.5 text-sm text-muted hover:text-primary transition-colors"
              >
                <ArrowLeft className="w-4 h-4" />
                Back to Inbox
              </button>
              <div className="flex-1">
                <StatsBar run={activeRun} />
              </div>
            </div>

            {/* Swarm header */}
            <div className="flex items-center gap-3">
              <Hexagon className="w-5 h-5 text-primary" />
              <h2 className="text-sm font-semibold text-golden uppercase tracking-wide">
                Swarm Activity
              </h2>
              {counts.review > 0 && (
                <span className="text-[10px] font-bold bg-warning/10 text-warning px-2 py-0.5 rounded-full border border-warning/20">
                  {counts.review} need{counts.review === 1 ? "s" : ""} review
                </span>
              )}
              {counts.working > 0 && (
                <span className="text-[10px] font-bold bg-honey-glow text-primary px-2 py-0.5 rounded-full border border-primary/20">
                  {counts.working} working
                </span>
              )}
              {counts.done > 0 && (
                <span className="text-[10px] font-bold bg-success/10 text-success px-2 py-0.5 rounded-full border border-success/20">
                  {counts.done} done
                </span>
              )}
              {/* Kill swarm button */}
              <div className="flex-1" />
              {isRunActive && (
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

            {/* Honeycomb beehive grid */}
            <LiveDraftPanel
              jobs={jobs}
              runId={activeRun.id}
              draftTokens={draftTokens}
              frameData={frameData}
              contentCache={contentCacheRef.current}
              onJobUpdated={handleJobUpdated}
            />

            {sortedJobs.length === 0 && (
              <div className="text-center py-16">
                <Loader2 className="w-6 h-6 animate-spin text-primary mx-auto mb-3" />
                <p className="text-sm text-muted">Deploying worker bees...</p>
              </div>
            )}
          </>
        ) : (
          <>
            {/* Inbox selector */}
            {authenticated && (
              <InboxSelector
                onRunCreated={handleRunCreated}
                disabled={isRunActive ? true : false}
              />
            )}

            {/* Not authenticated empty state */}
            {authenticated === false && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.3 }}
                className="text-center py-20"
              >
                <div className="w-16 h-16 hex-badge bg-honey-glow mx-auto mb-4 flex items-center justify-center">
                  <Hexagon className="w-8 h-8 text-primary" />
                </div>
                <h2 className="text-xl font-bold mb-2 text-golden">Welcome to Hive</h2>
                <p className="text-sm text-muted max-w-md mx-auto leading-relaxed">
                  Connect your Gmail account to deploy AI worker bees that will draft replies to your emails in parallel.
                </p>
              </motion.div>
            )}

            {/* Loading state */}
            {authenticated === null && (
              <div className="text-center py-20">
                <div className="w-10 h-10 hex-badge bg-honey-glow mx-auto mb-3 flex items-center justify-center animate-pulse">
                  <Hexagon className="w-5 h-5 text-primary" />
                </div>
                <p className="text-sm text-muted">Connecting to the Hive...</p>
              </div>
            )}
          </>
        )}

        {/* Past swarms — minimal summary instead of detailed runs */}
        {runs.length > 0 && !activeRun && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-muted" />
              <h3 className="text-sm font-semibold text-muted uppercase tracking-wide">
                Recent Swarms
              </h3>
            </div>
            <div className="space-y-1.5">
              {runs.slice(0, 8).map((run, i) => (
                <motion.button
                  key={run.id}
                  initial={{ opacity: 0, y: 6 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.03 }}
                  onClick={async () => {
                    setActiveRun(run);
                    setDraftTokens(new Map());
                    setChatMessages([]);
                    try {
                      const j = await listJobs(run.id);
                      setJobs(j);
                    } catch {
                      setJobs([]);
                    }
                  }}
                  className="w-full text-left flex items-center gap-3 px-4 py-2.5 rounded-xl border border-border bg-surface hover:border-primary/30 transition-all duration-200 hover-glow"
                >
                  <Hexagon className="w-3.5 h-3.5 text-primary/50 flex-shrink-0" />
                  <span className="text-xs text-muted flex-shrink-0">
                    {new Date(run.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                  </span>
                  <span className="text-sm text-foreground/80 flex-1 truncate">
                    {run.total_jobs} email{run.total_jobs !== 1 ? "s" : ""}
                    {run.completed_jobs > 0 && ` · ${run.completed_jobs} drafted`}
                    {run.failed_jobs > 0 && ` · ${run.failed_jobs} failed`}
                  </span>
                  <span className={`text-[10px] font-medium px-2 py-0.5 rounded-lg border ${
                    run.status === "completed" ? "bg-success/10 text-success border-success/20" :
                    run.status === "failed" ? "bg-error/10 text-error border-error/20" :
                    run.status === "running" ? "bg-honey-glow text-primary border-primary/20" :
                    "bg-surface-2 text-muted border-border"
                  }`}>
                    {run.status}
                  </span>
                </motion.button>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* Question chat */}
      <QuestionChat
        messages={chatMessages}
        runId={activeRun?.id || null}
        jobs={jobs}
        onAnswered={handleQuestionAnswered}
      />
    </div>
  );
}
