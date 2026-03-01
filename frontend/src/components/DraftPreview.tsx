"use client";

import { motion, AnimatePresence } from "framer-motion";
import { X, Mail, FileText, Clock, Hexagon, Hash } from "lucide-react";
import StepTimeline from "./StepTimeline";
import type { Job } from "@/lib/api";

interface DraftPreviewProps {
  job: Job | null;
  onClose: () => void;
}

export default function DraftPreview({ job, onClose }: DraftPreviewProps) {
  return (
    <AnimatePresence>
      {job && (
        <motion.div
          initial={{ opacity: 0, x: 30 }}
          animate={{ opacity: 1, x: 0 }}
          exit={{ opacity: 0, x: 30 }}
          transition={{ duration: 0.3, ease: "easeOut" }}
          className="fixed right-0 top-0 bottom-0 w-[420px] bg-surface border-l border-border shadow-2xl z-50 flex flex-col"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-surface-2">
            <div className="flex items-center gap-2">
              <Hexagon className="w-4 h-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">Worker Detail</span>
            </div>
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-honey-glow transition-all"
            >
              <X className="w-4 h-4 text-muted" />
            </button>
          </div>

          {/* Content */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {/* Subject */}
            <div>
              <div className="flex items-center gap-2 mb-1">
                <Mail className="w-3.5 h-3.5 text-muted" />
                <span className="text-xs text-muted uppercase tracking-wide">Subject</span>
              </div>
              <p className="text-sm font-medium">{job.subject || "(no subject)"}</p>
            </div>

            {/* Status badges */}
            <div className="flex flex-wrap gap-2">
              <span className={`text-xs font-medium px-2.5 py-1 rounded-lg border ${
                job.status === "completed" ? "bg-success/10 text-success border-success/20" :
                job.status === "failed" ? "bg-error/10 text-error border-error/20" :
                job.status === "running" ? "bg-honey-glow text-primary border-primary/20" :
                job.status === "skipped" ? "bg-warning/10 text-warning border-warning/20" :
                "bg-surface-2 text-muted border-border"
              }`}>
                {job.status}
              </span>
              {job.intent && (
                <span className="text-xs font-medium px-2.5 py-1 rounded-lg bg-surface-2 text-muted border border-border">
                  Intent: {job.intent}
                </span>
              )}
              {job.confidence != null && (
                <span className="text-xs font-mono px-2.5 py-1 rounded-lg bg-surface-2 text-muted border border-border">
                  {(job.confidence * 100).toFixed(1)}% confidence
                </span>
              )}
            </div>

            {/* Step timeline */}
            <div>
              <div className="flex items-center gap-2 mb-2">
                <Hexagon className="w-3.5 h-3.5 text-muted" />
                <span className="text-xs text-muted uppercase tracking-wide">Pipeline</span>
              </div>
              <StepTimeline steps={[]} currentStep={job.current_step} />
            </div>

            {/* Metadata */}
            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 rounded-xl bg-surface-2 border border-border">
                <div className="flex items-center gap-1.5 mb-1">
                  <Hash className="w-3 h-3 text-muted" />
                  <span className="text-[10px] text-muted uppercase">Tokens</span>
                </div>
                <span className="text-sm font-mono text-foreground">{job.tokens_used}</span>
              </div>
              <div className="p-3 rounded-xl bg-surface-2 border border-border">
                <div className="flex items-center gap-1.5 mb-1">
                  <Clock className="w-3 h-3 text-muted" />
                  <span className="text-[10px] text-muted uppercase">Duration</span>
                </div>
                <span className="text-sm font-mono text-foreground">{job.duration_ms ? `${(job.duration_ms / 1000).toFixed(1)}s` : "—"}</span>
              </div>
            </div>

            {/* Summary */}
            {job.summary && (
              <div>
                <span className="text-xs text-muted uppercase tracking-wide">Summary</span>
                <p className="text-sm mt-1 p-3 rounded-xl bg-surface-2 border border-border leading-relaxed">
                  {job.summary}
                </p>
              </div>
            )}

            {/* Error */}
            {job.error_msg && (
              <div>
                <span className="text-xs text-error uppercase tracking-wide">Error</span>
                <pre className="text-xs mt-1 p-3 rounded-xl bg-error/10 text-error font-mono overflow-x-auto whitespace-pre-wrap border border-error/20">
                  {job.error_msg}
                </pre>
              </div>
            )}

            {/* Draft ID */}
            {job.draft_id && (
              <div>
                <span className="text-xs text-muted uppercase tracking-wide">Draft ID</span>
                <p className="text-xs mt-1 p-2 rounded-xl bg-surface-2 font-mono text-muted break-all border border-border">
                  {job.draft_id}
                </p>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
