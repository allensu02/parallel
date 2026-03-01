"use client";

import { motion } from "framer-motion";
import { CheckCircle2, XCircle, SkipForward, Clock, Hexagon } from "lucide-react";
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
      icon: <Hexagon className="w-4 h-4" />,
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
      icon: <Clock className="w-4 h-4" />,
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
      className="glass-panel p-4 space-y-3"
    >
      {/* Golden progress bar — thick, vibrant, showy */}
      <div className="h-2.5 rounded-full bg-surface-2/60 overflow-hidden border border-honey/15 shadow-inner">
        <motion.div
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 0.8, ease: "easeOut" }}
          className="h-full rounded-full bg-gradient-to-r from-primary via-honey to-primary-light relative shadow-lg shadow-honey/30"
        >
          {/* Animated shimmer stripe */}
          <div className="absolute inset-0 overflow-hidden">
            <motion.div
              animate={{ x: ["-100%", "200%"] }}
              transition={{ duration: 1.5, repeat: Infinity, ease: "linear" }}
              className="w-1/3 h-full bg-gradient-to-r from-transparent via-white/30 to-transparent skew-x-12"
            />
          </div>
        </motion.div>
      </div>

      {/* Stats */}
      <div className="flex items-center gap-4">
        {stats.map((s) => (
          <motion.div
            key={s.label}
            whileHover={{ scale: 1.05 }}
            className="flex items-center gap-2 px-2 py-1 rounded-lg hover:bg-honey-glow/50 transition-colors"
          >
            <span className={s.color}>{s.icon}</span>
            <div>
              <motion.span
                key={s.value}
                initial={{ y: -6, opacity: 0, scale: 0.8 }}
                animate={{ y: 0, opacity: 1, scale: 1 }}
                transition={{ type: "spring", stiffness: 200, damping: 12 }}
                className={`text-lg font-bold font-mono ${s.color}`}
              >
                {s.value}
              </motion.span>
              <span className="text-xs text-muted/80 ml-1">{s.label}</span>
            </div>
          </motion.div>
        ))}

        <div className="ml-auto flex items-center gap-2">
          <span className={`text-xs font-bold px-3 py-1.5 rounded-lg border backdrop-blur-sm ${
            run.status === "running" ? "bg-honey/15 text-honey border-honey/30 animate-border-warm shadow-md shadow-honey/10" :
            run.status === "completed" ? "bg-success/15 text-success border-success/30" :
            run.status === "failed" ? "bg-error/15 text-error border-error/30" :
            "bg-surface-2 text-muted border-border"
          }`}>
            {run.status === "running" ? "Bees buzzing..." : run.status}
          </span>
        </div>
      </div>
    </motion.div>
  );
}
