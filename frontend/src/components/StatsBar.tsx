"use client";

import { motion } from "framer-motion";
import { CheckCircle2, XCircle, SkipForward, Loader2, Layers } from "lucide-react";
import type { Run } from "@/lib/api";

interface StatsBarProps {
  run: Run | null;
}

export default function StatsBar({ run }: StatsBarProps) {
  if (!run) return null;

  const stats = [
    {
      label: "Total",
      value: run.total_jobs,
      icon: <Layers className="w-4 h-4" />,
      color: "text-foreground",
    },
    {
      label: "Completed",
      value: run.completed_jobs,
      icon: <CheckCircle2 className="w-4 h-4" />,
      color: "text-success",
    },
    {
      label: "Failed",
      value: run.failed_jobs,
      icon: <XCircle className="w-4 h-4" />,
      color: "text-error",
    },
    {
      label: "Skipped",
      value: run.skipped_jobs,
      icon: <SkipForward className="w-4 h-4" />,
      color: "text-warning",
    },
    {
      label: "In Progress",
      value: Math.max(0, run.total_jobs - run.completed_jobs - run.failed_jobs - run.skipped_jobs),
      icon: <Loader2 className="w-4 h-4 animate-spin" />,
      color: "text-primary-light",
    },
  ];

  const progress = run.total_jobs > 0
    ? ((run.completed_jobs + run.failed_jobs + run.skipped_jobs) / run.total_jobs) * 100
    : 0;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="space-y-3"
    >
      {/* Progress bar */}
      <div className="h-2 rounded-full bg-surface-2 overflow-hidden">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.5, ease: "easeOut" }}
          className="h-full rounded-full bg-gradient-to-r from-primary to-primary-light"
        />
      </div>

      {/* Stats */}
      <div className="flex items-center gap-4">
        {stats.map((s) => (
          <div key={s.label} className="flex items-center gap-2">
            <span className={s.color}>{s.icon}</span>
            <div>
              <motion.span
                key={s.value}
                initial={{ y: -4, opacity: 0 }}
                animate={{ y: 0, opacity: 1 }}
                className={`text-lg font-bold font-mono ${s.color}`}
              >
                {s.value}
              </motion.span>
              <span className="text-xs text-muted ml-1">{s.label}</span>
            </div>
          </div>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <span className={`text-xs font-medium px-2 py-1 rounded-md ${
            run.status === "running" ? "bg-primary/15 text-primary-light" :
            run.status === "completed" ? "bg-success/15 text-success" :
            run.status === "failed" ? "bg-error/15 text-error" :
            "bg-surface-2 text-muted"
          }`}>
            {run.status}
          </span>
        </div>
      </div>
    </motion.div>
  );
}
