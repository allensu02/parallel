"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  ChevronDown, ChevronUp, Send,
  Calendar, Hexagon, Check, Mail, Loader2, ChevronRight,
} from "lucide-react";
import { answerQuestion, fetchThreadContent, type Job, type ThreadContent } from "@/lib/api";

export interface ChatMessage {
  id: string;
  type: "question" | "answer";
  jobId: string;
  subject: string;
  text: string;
  timestamp: string;
}

interface QuestionChatProps {
  messages: ChatMessage[];
  runId: string | null;
  jobs?: Job[];
  onAnswered?: (jobId: string) => void;
}

/* ─── Individual question card ─── */

function QuestionCard({
  question,
  answer,
  job,
  runId,
  onAnswered,
}: {
  question: ChatMessage;
  answer: ChatMessage | null;
  job: Job | undefined;
  runId: string | null;
  onAnswered?: (jobId: string) => void;
}) {
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [contextData, setContextData] = useState<ThreadContent | null>(null);
  const [contextLoading, setContextLoading] = useState(false);

  const isAnswered = !!answer;
  const isCalendarRelated = ["availability", "calendar", "schedule", "meeting", "free time", "when are you"]
    .some((k) => question.text.toLowerCase().includes(k));

  const handleSend = async () => {
    if (!input.trim() || !runId) return;
    setSending(true);
    try {
      await answerQuestion(runId, question.jobId, input.trim());
      onAnswered?.(question.jobId);
      setInput("");
    } catch {
      // ignore
    } finally {
      setSending(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const loadContext = useCallback(async () => {
    if (!job || contextData) return;
    setContextLoading(true);
    try {
      const data = await fetchThreadContent(job.thread_id);
      setContextData(data);
    } catch {
      // ignore
    } finally {
      setContextLoading(false);
    }
  }, [job, contextData]);

  const handleToggleContext = () => {
    if (!showContext && !contextData) {
      loadContext();
    }
    setShowContext(!showContext);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      className={`rounded-xl border overflow-hidden transition-all ${
        isAnswered
          ? "border-success/20 bg-surface"
          : "border-warning/25 bg-surface"
      }`}
    >
      {/* Question header — subject + status */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface-2 border-b border-border/50">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <div className={`w-5 h-5 hex-badge flex-shrink-0 flex items-center justify-center ${
            isAnswered ? "bg-success/15" : "bg-honey-glow"
          }`}>
            {isAnswered ? (
              <Check className="w-2.5 h-2.5 text-success" />
            ) : (
              <Hexagon className="w-2.5 h-2.5 text-primary" />
            )}
          </div>
          <span className="text-xs font-medium text-foreground truncate">
            {question.subject}
          </span>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          {isCalendarRelated && (
            <button className="flex items-center gap-1 text-[9px] text-primary hover:text-honey transition-colors px-1.5 py-0.5 rounded bg-honey-glow border border-primary/15">
              <Calendar className="w-2.5 h-2.5" />
              Calendar
            </button>
          )}
          <span className={`text-[9px] font-medium px-1.5 py-0.5 rounded ${
            isAnswered ? "bg-success/10 text-success" : "bg-warning/10 text-warning"
          }`}>
            {isAnswered ? "Answered" : "Pending"}
          </span>
        </div>
      </div>

      {/* Question body */}
      <div className="px-3 py-2.5">
        <div className="text-xs leading-relaxed text-foreground/90">
          {question.text}
        </div>
      </div>

      {/* Email context dropdown */}
      <div className="border-t border-border/30">
        <button
          onClick={handleToggleContext}
          className="w-full flex items-center gap-1.5 px-3 py-1.5 text-[10px] text-muted hover:text-primary transition-colors"
        >
          <Mail className="w-3 h-3" />
          <span>Email context</span>
          {showContext ? (
            <ChevronDown className="w-3 h-3 ml-auto" />
          ) : (
            <ChevronRight className="w-3 h-3 ml-auto" />
          )}
        </button>

        <AnimatePresence>
          {showContext && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.15 }}
              className="overflow-hidden"
            >
              <div className="px-3 pb-2.5 space-y-2">
                {contextLoading ? (
                  <div className="flex items-center gap-1.5 text-[10px] text-muted py-1">
                    <Loader2 className="w-3 h-3 animate-spin text-primary" />
                    Loading email...
                  </div>
                ) : contextData ? (
                  <div className="max-h-[140px] overflow-y-auto rounded-lg bg-surface-2 border border-border/50 p-2 space-y-2">
                    {contextData.messages.map((msg, i) => (
                      <div key={i} className="text-[10px]">
                        <div className="font-medium text-foreground/80 mb-0.5">
                          {msg.from} <span className="text-muted font-normal ml-1">{msg.date}</span>
                        </div>
                        <div className="text-muted whitespace-pre-wrap leading-relaxed">
                          {msg.body.slice(0, 400)}
                          {msg.body.length > 400 && "..."}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : job ? (
                  <div className="text-[10px] text-muted rounded-lg bg-surface-2 border border-border/50 p-2">
                    <div className="font-medium text-foreground/70 mb-0.5">Thread: {job.subject}</div>
                    <div>Intent: {job.intent || "unknown"} · Status: {job.status}</div>
                  </div>
                ) : (
                  <div className="text-[10px] text-muted">No context available</div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* Answer or input */}
      {isAnswered ? (
        <div className="px-3 py-2 border-t border-border/50 bg-success/5">
          <div className="text-[10px] text-muted mb-0.5">Your answer:</div>
          <div className="text-xs text-success">{answer.text === "(answered)" ? "Answered" : answer.text}</div>
        </div>
      ) : (
        <div className="px-3 py-2 border-t border-border/50 flex items-center gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your answer..."
            disabled={sending}
            className="flex-1 px-2.5 py-1.5 text-xs rounded-lg bg-surface-2 border border-border focus:border-primary focus:ring-1 focus:ring-primary/20 outline-none text-foreground disabled:opacity-50 transition-all"
          />
          <button
            onClick={handleSend}
            disabled={!input.trim() || sending}
            className="p-1.5 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey disabled:opacity-40 disabled:cursor-not-allowed transition-all text-background"
          >
            <Send className="w-3 h-3" />
          </button>
        </div>
      )}
    </motion.div>
  );
}


/* ─── Main panel ─── */

export default function QuestionChat({ messages, runId, jobs, onAnswered }: QuestionChatProps) {
  const [collapsed, setCollapsed] = useState(false);

  // Auto-expand when new question arrives
  useEffect(() => {
    const pendingQ = messages.filter(
      (m) => m.type === "question" && !messages.some((a) => a.type === "answer" && a.jobId === m.jobId)
    );
    if (pendingQ.length > 0) {
      setCollapsed(false);
    }
  }, [messages]);

  // Group questions by jobId
  const questions = messages.filter((m) => m.type === "question");
  const answers = messages.filter((m) => m.type === "answer");

  const pendingCount = questions.filter(
    (q) => !answers.some((a) => a.jobId === q.jobId)
  ).length;

  if (messages.length === 0) return null;

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className="fixed bottom-0 left-0 right-0 z-40"
    >
      <div className="max-w-7xl mx-auto px-6">
        <div className="rounded-t-xl border border-b-0 border-border bg-surface shadow-2xl shadow-black/30 overflow-hidden">
          {/* Toggle header */}
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="w-full flex items-center justify-between px-4 py-2.5 bg-surface-2 hover:bg-border/30 transition-all"
          >
            <div className="flex items-center gap-2">
              <Hexagon className="w-4 h-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">Hive Needs Input</span>
              {pendingCount > 0 && (
                <span className="text-[10px] font-bold bg-honey-glow text-honey px-2 py-0.5 rounded-full border border-honey/30">
                  {pendingCount} pending
                </span>
              )}
              {questions.length > pendingCount && (
                <span className="text-[10px] text-success px-1.5 py-0.5 rounded-full bg-success/10 border border-success/20">
                  {questions.length - pendingCount} answered
                </span>
              )}
            </div>
            {collapsed ? (
              <ChevronUp className="w-4 h-4 text-muted" />
            ) : (
              <ChevronDown className="w-4 h-4 text-muted" />
            )}
          </button>

          {/* Question cards */}
          <AnimatePresence>
            {!collapsed && (
              <motion.div
                initial={{ height: 0 }}
                animate={{ height: "auto" }}
                exit={{ height: 0 }}
                transition={{ duration: 0.2 }}
                className="overflow-hidden"
              >
                <div className="max-h-[350px] overflow-y-auto px-3 py-3 space-y-2">
                  {/* Pending questions first, then answered */}
                  {[...questions]
                    .sort((a, b) => {
                      const aAnswered = answers.some((ans) => ans.jobId === a.jobId);
                      const bAnswered = answers.some((ans) => ans.jobId === b.jobId);
                      if (aAnswered !== bAnswered) return aAnswered ? 1 : -1;
                      return 0;
                    })
                    .map((q) => {
                      const ans = answers.find((a) => a.jobId === q.jobId) ?? null;
                      const job = jobs?.find((j) => j.id === q.jobId);
                      return (
                        <QuestionCard
                          key={q.id}
                          question={q}
                          answer={ans}
                          job={job}
                          runId={runId}
                          onAnswered={onAnswered}
                        />
                      );
                    })}
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}
