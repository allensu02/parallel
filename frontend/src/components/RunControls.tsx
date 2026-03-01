"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Play, Loader2 } from "lucide-react";
import { createRun, type Run } from "@/lib/api";

interface RunControlsProps {
  onRunCreated: (run: Run) => void;
  disabled?: boolean;
}

export default function RunControls({ onRunCreated, disabled }: RunControlsProps) {
  const [loading, setLoading] = useState(false);
  const [threadCount, setThreadCount] = useState(50);

  const handleStart = async () => {
    setLoading(true);
    try {
      const run = await createRun(threadCount);
      onRunCreated(run);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Unknown error";
      alert(`Failed to start run: ${message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.1 }}
      className="flex items-center gap-4 p-4 rounded-xl bg-surface border border-border"
    >
      <div className="flex items-center gap-2">
        <label className="text-sm text-muted">Threads:</label>
        <input
          type="number"
          min={1}
          max={500}
          value={threadCount}
          onChange={(e) => setThreadCount(Number(e.target.value))}
          className="w-20 px-2 py-1.5 text-sm rounded-md bg-surface-2 border border-border focus:border-primary outline-none text-foreground font-mono"
        />
      </div>

      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={handleStart}
        disabled={loading || disabled}
        className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-primary hover:bg-primary-light disabled:opacity-50 disabled:cursor-not-allowed transition-all text-white font-semibold text-sm"
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Play className="w-4 h-4" />
        )}
        Run Inbox Zero
      </motion.button>
    </motion.div>
  );
}
