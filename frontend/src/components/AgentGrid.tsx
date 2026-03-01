"use client";

import { useEffect, useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Zap, AlertTriangle, CheckCircle2, Clock, SkipForward } from "lucide-react";
import StepTimeline from "./StepTimeline";
import type { Job } from "@/lib/api";

interface AgentGridProps {
  jobs: Job[];
  onSelectJob?: (job: Job) => void;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Zap className="w-4 h-4 text-primary-light" />,
  completed: <CheckCircle2 className="w-4 h-4 text-success" />,
  failed: <AlertTriangle className="w-4 h-4 text-error" />,
  queued: <Clock className="w-4 h-4 text-muted" />,
  skipped: <SkipForward className="w-4 h-4 text-warning" />,
};

export default function AgentGrid({ jobs, onSelectJob }: AgentGridProps) {
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 9;

  // Auto-cycle through pages showing active jobs
  const activeJobs = useMemo(() => jobs.filter((j) => j.status === "running"), [jobs]);
  const displayJobs = useMemo(() => {
    // Prioritize: running first, then recently completed, then queued
    const sorted = [...jobs].sort((a, b) => {
      const order: Record<string, number> = { running: 0, failed: 2, completed: 3, skipped: 4, queued: 1 };
      return (order[a.status] ?? 5) - (order[b.status] ?? 5);
    });
    const start = page * PAGE_SIZE;
    return sorted.slice(start, start + PAGE_SIZE);
  }, [jobs, page]);

  const totalPages = Math.ceil(jobs.length / PAGE_SIZE);

  // Auto-advance page every 5 seconds if there are multiple pages
  useEffect(() => {
    if (totalPages <= 1) return;
    const timer = setInterval(() => {
      setPage((p) => (p + 1) % totalPages);
    }, 5000);
    return () => clearInterval(timer);
  }, [totalPages]);

  // Fill empty cells to always show 3x3
  const cells = [...displayJobs];
  while (cells.length < 9) {
    cells.push(null as unknown as Job);
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-3 gap-3">
        <AnimatePresence mode="popLayout">
          {cells.map((job, i) => (
            <motion.div
              key={job?.id ?? `empty-${i}`}
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.9 }}
              transition={{ delay: i * 0.04, duration: 0.25, ease: "easeOut" }}
              className={`rounded-xl border p-4 min-h-[140px] transition-all duration-300 ${
                job
                  ? "bg-surface border-border hover:border-primary/50 cursor-pointer"
                  : "bg-surface-2/50 border-border/50"
              }`}
              onClick={() => job && onSelectJob?.(job)}
            >
              {job ? (
                <div className="flex flex-col gap-2 h-full">
                  <div className="flex items-start justify-between">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      {STATUS_ICON[job.status]}
                      <span className="text-xs font-medium truncate">
                        {job.subject || job.thread_id.slice(0, 12)}
                      </span>
                    </div>
                    {job.confidence != null && (
                      <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
                        job.confidence >= 0.8 ? "bg-success/15 text-success" :
                        job.confidence >= 0.6 ? "bg-warning/15 text-warning" :
                        "bg-error/15 text-error"
                      }`}>
                        {(job.confidence * 100).toFixed(0)}%
                      </span>
                    )}
                  </div>

                  <StepTimeline
                    steps={[]}
                    currentStep={job.current_step}
                    compact
                  />

                  {job.intent && (
                    <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full self-start ${
                      job.intent === "reply" ? "bg-primary/15 text-primary-light" :
                      job.intent === "ignore" ? "bg-muted/15 text-muted" :
                      "bg-warning/15 text-warning"
                    }`}>
                      {job.intent}
                    </span>
                  )}

                  {job.summary && (
                    <p className="text-[10px] text-muted line-clamp-2 mt-auto">
                      {job.summary}
                    </p>
                  )}

                  {job.error_msg && (
                    <p className="text-[10px] text-error line-clamp-1 mt-auto">
                      {job.error_msg}
                    </p>
                  )}
                </div>
              ) : (
                <div className="flex items-center justify-center h-full text-muted/30">
                  <Mail className="w-6 h-6" />
                </div>
              )}
            </motion.div>
          ))}
        </AnimatePresence>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2">
          {Array.from({ length: Math.min(totalPages, 10) }).map((_, i) => (
            <button
              key={i}
              onClick={() => setPage(i)}
              className={`w-2 h-2 rounded-full transition-all duration-300 ${
                i === page ? "bg-primary w-6" : "bg-border hover:bg-muted"
              }`}
            />
          ))}
        </div>
      )}
    </div>
  );
}
