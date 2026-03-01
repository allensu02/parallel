"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { motion } from "framer-motion";
import { ArrowLeft, LayoutGrid, List } from "lucide-react";
import Link from "next/link";
import Header from "@/components/Header";
import StatsBar from "@/components/StatsBar";
import AgentGrid from "@/components/AgentGrid";
import JobRow from "@/components/JobRow";
import DraftPreview from "@/components/DraftPreview";
import { useSSE, type SSEMessage } from "@/hooks/useSSE";
import { type Run, type Job, getRun, listJobs, getSSEUrl } from "@/lib/api";

type ViewMode = "grid" | "list";

export default function RunDetailPage() {
  const params = useParams<{ id: string }>();
  const runId = params.id;

  const [run, setRun] = useState<Run | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("grid");
  const [screenshots, setScreenshots] = useState<Map<string, string>>(new Map());

  useEffect(() => {
    getRun(runId).then(setRun).catch(() => {});
    listJobs(runId).then(setJobs).catch(() => {});
  }, [runId]);

  useEffect(() => {
    if (!run || run.status === "completed" || run.status === "failed") return;
    const timer = setInterval(async () => {
      const [updatedRun, updatedJobs] = await Promise.all([
        getRun(runId),
        listJobs(runId),
      ]);
      setRun(updatedRun);
      setJobs(updatedJobs);
    }, 2000);
    return () => clearInterval(timer);
  }, [run, runId]);

  const handleSSE = useCallback((msg: SSEMessage) => {
    const { event, data } = msg;
    if (event.startsWith("run.")) {
      setRun((prev) => prev ? { ...prev, ...data } as Run : prev);
    }
    if (event.startsWith("job.") && !event.startsWith("job.screenshot")) {
      const jobId = data.job_id as string;
      setJobs((prev) => {
        const idx = prev.findIndex((j) => j.id === jobId);
        if (idx === -1) return prev;
        const updated = [...prev];
        updated[idx] = { ...updated[idx], ...data } as Job;
        return updated;
      });
    }
    if (event === "job.screenshot") {
      setScreenshots((prev) => {
        const next = new Map(prev);
        next.set(data.job_id as string, data.url as string);
        return next;
      });
    }
  }, []);

  useSSE(getSSEUrl(runId), handleSSE);

  return (
    <div className="min-h-screen flex flex-col">
      <Header onAuthChange={() => {}} />

      <main className="flex-1 max-w-7xl mx-auto w-full px-6 py-6 space-y-6">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="p-2 rounded-lg bg-surface border border-border hover:border-primary/30 transition-colors"
          >
            <ArrowLeft className="w-4 h-4 text-muted" />
          </Link>
          <div>
            <span className="text-xs text-muted font-mono">Run {runId}</span>
            {run && (
              <p className="text-xs text-muted">
                Started {new Date(run.created_at).toLocaleString()}
              </p>
            )}
          </div>
        </div>

        <StatsBar run={run} />

        {jobs.length > 0 && (
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

            {viewMode === "grid" ? (
              <AgentGrid jobs={jobs} screenshots={screenshots} onSelectJob={setSelectedJob} />
            ) : (
              <div className="space-y-2">
                {jobs.map((job, i) => (
                  <JobRow
                    key={job.id}
                    job={job}
                    index={i}
                    onClick={() => setSelectedJob(job)}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </main>

      <DraftPreview job={selectedJob} onClose={() => setSelectedJob(null)} />
    </div>
  );
}
