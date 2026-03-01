"use client";

import { motion } from "framer-motion";
import { Check, X, Loader2, Hexagon } from "lucide-react";

const STEP_LABELS: Record<string, string> = {
  fetch_thread: "Fetch",
  classify_intent: "Classify",
  generate_draft: "Draft",
  save_draft: "Save",
  apply_label: "Label",
};

const ALL_STEPS = ["fetch_thread", "classify_intent", "generate_draft", "save_draft", "apply_label"];

interface StepInfo {
  name: string;
  status: "pending" | "running" | "completed" | "failed" | "skipped";
  duration_ms?: number;
}

interface StepTimelineProps {
  steps: StepInfo[];
  currentStep?: string;
  compact?: boolean;
}

export default function StepTimeline({ steps, currentStep, compact = false }: StepTimelineProps) {
  const stepMap = new Map(steps.map((s) => [s.name, s]));

  return (
    <div className="flex items-center gap-1">
      {ALL_STEPS.map((name, i) => {
        const step = stepMap.get(name);
        const status = step?.status || (currentStep === name ? "running" : "pending");
        const label = STEP_LABELS[name] || name;

        return (
          <div key={name} className="flex items-center">
            {i > 0 && (
              <div
                className={`h-px transition-all duration-500 ${compact ? "w-2" : "w-4"} ${
                  status === "completed" ? "bg-success" :
                  status === "failed" ? "bg-error" :
                  "bg-border"
                }`}
              />
            )}
            <motion.div
              initial={{ scale: 0.8, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              transition={{ delay: i * 0.05, duration: 0.2 }}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-medium transition-all duration-300 ${
                status === "completed"
                  ? "bg-success/15 text-success"
                  : status === "failed"
                  ? "bg-error/15 text-error"
                  : status === "running"
                  ? "bg-honey-glow text-primary animate-honey-glow"
                  : status === "skipped"
                  ? "bg-surface-2 text-muted line-through"
                  : "bg-surface-2 text-muted"
              }`}
              title={step?.duration_ms ? `${step.duration_ms}ms` : undefined}
            >
              {status === "completed" && <Check className="w-3 h-3" />}
              {status === "failed" && <X className="w-3 h-3" />}
              {status === "running" && <Loader2 className="w-3 h-3 animate-spin" />}
              {status === "pending" && <Hexagon className="w-2.5 h-2.5" />}
              {!compact && <span>{label}</span>}
              {step?.duration_ms && !compact ? (
                <span className="text-[10px] opacity-60">{step.duration_ms}ms</span>
              ) : null}
            </motion.div>
          </div>
        );
      })}
    </div>
  );
}
