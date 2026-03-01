"use client";

import { useEffect, useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Mail, Hexagon, AlertTriangle, CheckCircle2, Clock, SkipForward } from "lucide-react";
import StepTimeline from "./StepTimeline";
import type { Job } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AgentGridProps {
  jobs: Job[];
  screenshots: Map<string, string>;
  onSelectJob?: (job: Job) => void;
}

const STATUS_ICON: Record<string, React.ReactNode> = {
  running: <Hexagon className="w-4 h-4 text-primary-light" />,
  completed: <CheckCircle2 className="w-4 h-4 text-success" />,
  failed: <AlertTriangle className="w-4 h-4 text-error" />,
  queued: <Clock className="w-4 h-4 text-muted" />,
  skipped: <SkipForward className="w-4 h-4 text-warning" />,
};

export default function AgentGrid({ jobs, screenshots, onSelectJob }: AgentGridProps) {
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 9;

  const displayJobs = useMemo(() => {
    const sorted = [...jobs].sort((a, b) => {
      const order: Record<string, number> = { running: 0, failed: 2, completed: 3, skipped: 4, queued: 1 };
      return (order[a.status] ?? 5) - (order[b.status] ?? 5);
    });
    const start = page * PAGE_SIZE;
    return sorted.slice(start, start + PAGE_SIZE);
  }, [jobs, page]);

  const totalPages = Math.ceil(jobs.length / PAGE_SIZE);

  useEffect(() => {
    if (totalPages <= 1) return;
    const timer = setInterval(() => {
      setPage((p) => (p + 1) % totalPages);
    }, 6000);
    return () => clearInterval(timer);
  }, [totalPages]);

  const cells = [...displayJobs];
  while (cells.length < 9) {
    cells.push(null as unknown as Job);
  }

  return (
    <div className="space-y-4">
      <div className="honeycomb-grid">
        <AnimatePresence mode="popLayout">
          {cells.map((job, i) => {
            const screenshotUrl = job ? screenshots.get(job.id) : undefined;

            return (
              <motion.div
                key={job?.id ?? `empty-${i}`}
                initial={{ opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.9 }}
                transition={{ delay: i * 0.04, duration: 0.25, ease: "easeOut" }}
                className={`rounded-xl border overflow-hidden min-h-[180px] transition-all duration-300 hover-glow ${
                  job
                    ? "bg-surface border-border hover:border-primary/50 cursor-pointer"
                    : "bg-surface-2/50 border-border/50"
                }`}
                onClick={() => job && onSelectJob?.(job)}
              >
                {job ? (
                  <div className="flex flex-col h-full">
                    {/* Screenshot area */}
                    {screenshotUrl ? (
                      <div className="relative h-[100px] bg-black/20 overflow-hidden">
                        <img
                          src={`${API_BASE}${screenshotUrl}`}
                          alt={`Agent ${job.id}`}
                          className="w-full h-full object-cover object-top opacity-80"
                        />
                        <div className="absolute inset-0 bg-gradient-to-b from-transparent to-surface" />
                      </div>
                    ) : (
                      <div className="h-[60px] bg-surface-2/50 flex items-center justify-center">
                        {job.status === "running" ? (
                          <div className="w-6 h-6 rounded-full border-2 border-primary border-t-transparent animate-spin" />
                        ) : (
                          <Hexagon className="w-5 h-5 text-muted/30" />
                        )}
                      </div>
                    )}

                    {/* Info area */}
                    <div className="flex flex-col gap-1.5 p-3 flex-1">
                      <div className="flex items-start justify-between">
                        <div className="flex items-center gap-1.5 min-w-0 flex-1">
                          {STATUS_ICON[job.status]}
                          <span className="text-xs font-medium truncate">
                            {job.subject || job.thread_id.slice(0, 12)}
                          </span>
                        </div>
                        {job.confidence != null && (
                          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded-lg ${
                            job.confidence >= 0.8 ? "bg-success/15 text-success" :
                            job.confidence >= 0.6 ? "bg-warning/15 text-warning" :
                            "bg-error/15 text-error"
                          }`}>
                            {(job.confidence * 100).toFixed(0)}%
                          </span>
                        )}
                      </div>

                      <StepTimeline steps={[]} currentStep={job.current_step} compact />

                      {job.intent && (
                        <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full self-start ${
                          job.intent === "reply" ? "bg-honey-glow text-primary" :
                          job.intent === "ignore" ? "bg-muted/15 text-muted" :
                          "bg-warning/15 text-warning"
                        }`}>
                          {job.intent}
                        </span>
                      )}

                      {job.summary && (
                        <p className="text-[10px] text-muted line-clamp-2 mt-auto">{job.summary}</p>
                      )}

                      {job.error_msg && (
                        <p className="text-[10px] text-error line-clamp-1 mt-auto">{job.error_msg}</p>
                      )}
                    </div>
                  </div>
                ) : (
                  <div className="flex items-center justify-center h-full text-muted/20">
                    <Hexagon className="w-6 h-6" />
                  </div>
                )}
              </motion.div>
            );
          })}
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
