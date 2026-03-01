"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Hexagon, Loader2 } from "lucide-react";
import { createRun, type Run } from "@/lib/api";

interface RunControlsProps {
  onRunCreated: (run: Run) => void;
  disabled?: boolean;
}

export default function RunControls({ onRunCreated, disabled }: RunControlsProps) {
  const [loading, setLoading] = useState(false);
  const [threadInput, setThreadInput] = useState("50");

  const handleStart = async () => {
    setLoading(true);
    try {
      const run = await createRun([]);
      onRunCreated(run);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Unknown error";
      alert(`Failed to deploy swarm: ${message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: 0.1 }}
      className="flex items-center gap-4 p-4 rounded-xl bg-surface border border-border hover-glow"
    >
      <div className="flex items-center gap-2">
        <label className="text-sm text-muted">Workers:</label>
        <input
          type="text"
          inputMode="numeric"
          pattern="[0-9]*"
          value={threadInput}
          onChange={(e) => {
            const v = e.target.value.replace(/[^0-9]/g, "");
            setThreadInput(v);
          }}
          onFocus={(e) => e.target.select()}
          onBlur={() => {
            const n = parseInt(threadInput, 10);
            if (!n || n < 1) setThreadInput("1");
            else if (n > 500) setThreadInput("500");
            else setThreadInput(String(n));
          }}
          className="w-20 px-2 py-1.5 text-sm rounded-lg bg-surface-2 border border-border focus:border-primary focus:ring-1 focus:ring-primary/20 outline-none text-foreground font-mono text-center transition-all"
        />
      </div>

      <motion.button
        whileHover={{ scale: 1.02 }}
        whileTap={{ scale: 0.98 }}
        onClick={handleStart}
        disabled={loading || disabled}
        className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey disabled:opacity-50 disabled:cursor-not-allowed transition-all text-background font-semibold text-sm shadow-lg shadow-primary/20"
      >
        {loading ? (
          <Loader2 className="w-4 h-4 animate-spin" />
        ) : (
          <Hexagon className="w-4 h-4" />
        )}
        Deploy Swarm
      </motion.button>
    </motion.div>
  );
}
