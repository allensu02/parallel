"use client";

import { useCallback, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { LayoutGrid, List, History } from "lucide-react";
import Header from "@/components/Header";
import RunControls from "@/components/RunControls";
import StatsBar from "@/components/StatsBar";
import AgentGrid from "@/components/AgentGrid";
import JobRow from "@/components/JobRow";
import DraftPreview from "@/components/DraftPreview";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import {
  type Run,
  type Job,
  getSSEUrl,
  getRun,
  listJobs,
  listRuns,
  getAuthStatus,
} from "@/lib/api";

type ViewMode = "grid" | "list";

export default function Dashboard() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");

  // Check auth on mount
  useEffect(() => {
    getAuthStatus()
      .then((s) => setAuthenticated(s.authenticated))
      .catch(() => setAuthenticated(false));
    listRuns()
      .then(setRuns)
      .catch(() => {});
  }, []);

  // Polling for run updates
  useEffect(() => {
    if (!activeRun || activeRun.status === "completed" || activeRun.status === "failed") return;
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
    }, 2000);
    return () => clearInterval(timer);
  }, [activeRun]);

  // SSE for real-time updates
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

      if (event.startsWith("job.")) {
        const jobId = data.job_id as string;
        setJobs((prev) => {
          const idx = prev.findIndex((j) => j.id === jobId);
          if (idx === -1) return prev;
          const updated = [...prev];
          updated[idx] = { ...updated[idx], ...data } as Job;
          return updated;
        });
      }
    },
    []
  );

  useSSE(sseUrl, handleSSE);

  // Handle new run created
  const handleRunCreated = useCallback(async (run: Run) => {
    setActiveRun(run);
    setJobs([]);
    setRuns((prev) => [run, ...prev]);
    // Start polling jobs after a short delay to let them be created
    setTimeout(async () => {
      try {
        const jobs = await listJobs(run.id);
        setJobs(jobs);
      } catch {
        // ignore
      }
    }, 1500);
  }, []);

  // Load a past run
  const handleLoadRun = useCallback(async (run: Run) => {
    setActiveRun(run);
    try {
      const jobs = await listJobs(run.id);
      setJobs(jobs);
    } catch {
      setJobs([]);
    }
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      <Header />

      <main className="flex-1 max-w-7xl mx-auto w-full px-6 py-6 space-y-6">
        {/* Controls */}
        <RunControls
          onRunCreated={handleRunCreated}
          disabled={!authenticated || (activeRun?.status === "running")}
        />

        {/* Active run stats */}
        {activeRun && <StatsBar run={activeRun} />}

        {/* View toggle + content */}
        {activeRun && jobs.length > 0 && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold text-muted uppercase tracking-wide">
                Agents ({jobs.length})
              </h2>
              <div className="flex items-center gap-1 bg-surface rounded-lg p-1 border border-border">
                <button
                  onClick={() => setViewMode("grid")}
                  className={`p-1.5 rounded-md transition-colors ${
                    viewMode === "grid" ? "bg-primary text-white" : "text-muted hover:text-foreground"
                  }`}
                >
                  <LayoutGrid className="w-4 h-4" />
                </button>
                <button
                  onClick={() => setViewMode("list")}
                  className={`p-1.5 rounded-md transition-colors ${
                    viewMode === "list" ? "bg-primary text-white" : "text-muted hover:text-foreground"
                  }`}
                >
                  <List className="w-4 h-4" />
                </button>
              </div>
            </div>

            <AnimatePresence mode="wait">
              {viewMode === "grid" ? (
                <motion.div
                  key="grid"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                >
                  <AgentGrid jobs={jobs} onSelectJob={setSelectedJob} />
                </motion.div>
              ) : (
                <motion.div
                  key="list"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.2 }}
                  className="space-y-2"
                >
                  {jobs.map((job, i) => (
                    <JobRow
                      key={job.id}
                      job={job}
                      index={i}
                      onClick={() => setSelectedJob(job)}
                    />
                  ))}
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        )}

        {/* Empty state */}
        {!activeRun && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.3 }}
            className="text-center py-20"
          >
            <div className="w-16 h-16 rounded-2xl bg-surface-2 flex items-center justify-center mx-auto mb-4">
              <LayoutGrid className="w-8 h-8 text-muted" />
            </div>
            <h2 className="text-lg font-semibold mb-2">No active run</h2>
            <p className="text-sm text-muted max-w-md mx-auto">
              {authenticated
                ? "Click \"Run Inbox Zero\" above to start processing your email threads with parallel agents."
                : "Connect your Gmail account first, then start a run to process emails in parallel."}
            </p>
          </motion.div>
        )}

        {/* Past runs */}
        {runs.length > 0 && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <History className="w-4 h-4 text-muted" />
              <h3 className="text-sm font-semibold text-muted uppercase tracking-wide">
                Past Runs
              </h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {runs.slice(0, 6).map((run, i) => (
                <motion.button
                  key={run.id}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.05 }}
                  onClick={() => handleLoadRun(run)}
                  className={`text-left p-4 rounded-xl border transition-all duration-200 ${
                    activeRun?.id === run.id
                      ? "bg-primary/10 border-primary/30"
                      : "bg-surface border-border hover:border-primary/30"
                  }`}
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-xs font-mono text-muted">{run.id}</span>
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded ${
                      run.status === "completed" ? "bg-success/15 text-success" :
                      run.status === "failed" ? "bg-error/15 text-error" :
                      run.status === "running" ? "bg-primary/15 text-primary-light" :
                      "bg-surface-2 text-muted"
                    }`}>
                      {run.status}
                    </span>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted">
                    <span>{run.total_jobs} threads</span>
                    <span>{run.completed_jobs} done</span>
                    {run.failed_jobs > 0 && <span className="text-error">{run.failed_jobs} failed</span>}
                  </div>
                  <div className="text-[10px] text-muted mt-1">
                    {new Date(run.created_at).toLocaleString()}
                  </div>
                </motion.button>
              ))}
            </div>
          </div>
        )}
      </main>

      {/* Draft preview sidebar */}
      <DraftPreview job={selectedJob} onClose={() => setSelectedJob(null)} />
    </div>
  );
}
