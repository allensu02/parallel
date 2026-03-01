"use client";

import { motion } from "framer-motion";
import { Hexagon, AlertTriangle, CheckCircle2, Clock, SkipForward } from "lucide-react";
import StepTimeline from "./StepTimeline";
import type { Job } from "@/lib/api";

interface JobRowProps {
  job: Job;
  index: number;
  onClick?: () => void;
}

const STATUS_CONFIG: Record<string, { icon: React.ReactNode; bg: string }> = {
  running: { icon: <Hexagon className="w-3.5 h-3.5 text-primary-light" />, bg: "border-l-primary" },
  completed: { icon: <CheckCircle2 className="w-3.5 h-3.5 text-success" />, bg: "border-l-success" },
  failed: { icon: <AlertTriangle className="w-3.5 h-3.5 text-error" />, bg: "border-l-error" },
  queued: { icon: <Clock className="w-3.5 h-3.5 text-muted" />, bg: "border-l-border" },
  skipped: { icon: <SkipForward className="w-3.5 h-3.5 text-warning" />, bg: "border-l-warning" },
};

export default function JobRow({ job, index, onClick }: JobRowProps) {
  const config = STATUS_CONFIG[job.status] || STATUS_CONFIG.queued;

  return (
    <motion.div
      initial={{ opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: Math.min(index * 0.02, 1), duration: 0.25 }}
      onClick={onClick}
      className={`flex items-center gap-4 px-4 py-3 rounded-xl bg-surface border border-border border-l-2 ${config.bg} hover:bg-surface-2 cursor-pointer transition-all duration-200 hover-glow`}
    >
      {/* Status icon */}
      <div className="flex-shrink-0">{config.icon}</div>

      {/* Subject */}
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium truncate">
          {job.subject || job.thread_id.slice(0, 16)}
        </p>
        <div className="flex items-center gap-2 mt-0.5">
          {job.intent && (
            <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${
              job.intent === "reply" ? "bg-honey-glow text-primary" :
              job.intent === "ignore" ? "bg-muted/15 text-muted" :
              "bg-warning/15 text-warning"
            }`}>
              {job.intent}
            </span>
          )}
          {job.error_msg && (
            <span className="text-[10px] text-error truncate max-w-[200px]">
              {job.error_msg}
            </span>
          )}
        </div>
      </div>

      {/* Step timeline */}
      <div className="hidden md:block">
        <StepTimeline steps={[]} currentStep={job.current_step} compact />
      </div>

      {/* Metadata */}
      <div className="flex items-center gap-3 text-xs text-muted flex-shrink-0">
        {job.tokens_used > 0 && (
          <span className="font-mono">{job.tokens_used} tok</span>
        )}
        {job.confidence != null && (
          <span className={`font-mono ${
            job.confidence >= 0.8 ? "text-success" :
            job.confidence >= 0.6 ? "text-warning" :
            "text-error"
          }`}>
            {(job.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>
    </motion.div>
  );
}
