"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Mail, ChevronDown, ChevronRight, Loader2, Hexagon,
  CheckSquare, Square, RefreshCw, Search,
} from "lucide-react";
import {
  fetchInboxThreads,
  fetchThreadContent,
  batchFetchThreads,
  type InboxThread,
  type ThreadContent,
  type Run,
  createRun,
} from "@/lib/api";

interface InboxSelectorProps {
  onRunCreated: (run: Run, contentCache: Record<string, ThreadContent>) => void;
  disabled?: boolean;
}

export default function InboxSelector({ onRunCreated, disabled }: InboxSelectorProps) {
  const [threads, setThreads] = useState<InboxThread[]>([]);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<string | null>(null);
  const [expandLoading, setExpandLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  // Persistent content cache — survives expand/collapse
  const contentCacheRef = useRef<Record<string, ThreadContent>>({});
  const [cacheKeys, setCacheKeys] = useState<Set<string>>(new Set());

  const prefetchingRef = useRef<Set<string>>(new Set());

  const loadThreads = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchInboxThreads(50);
      setThreads(data);
      // Eagerly pre-fetch all thread content in background so expand is instant
      const ids = data.map((t) => t.id).filter(
        (id) => !contentCacheRef.current[id] && !prefetchingRef.current.has(id)
      );
      if (ids.length > 0) {
        // Mark as in-flight
        for (const id of ids) prefetchingRef.current.add(id);
        batchFetchThreads(ids)
          .then((results) => {
            for (const [id, content] of Object.entries(results)) {
              if (content) contentCacheRef.current[id] = content;
              prefetchingRef.current.delete(id);
            }
            setCacheKeys(new Set(Object.keys(contentCacheRef.current)));
          })
          .catch(() => {
            for (const id of ids) prefetchingRef.current.delete(id);
          });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Failed to load inbox";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadThreads();
  }, [loadThreads]);

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set(filteredThreads.map((t) => t.id)));
  };

  const deselectAll = () => {
    setSelected(new Set());
  };

  const getCached = (id: string): ThreadContent | undefined =>
    contentCacheRef.current[id];

  const toggleExpand = async (id: string) => {
    if (expanded === id) {
      setExpanded(null);
      return;
    }
    setExpanded(id);

    // Already cached — instant
    if (getCached(id)) return;

    // If background prefetch is already running, poll for it
    if (prefetchingRef.current.has(id)) {
      setExpandLoading(true);
      const start = Date.now();
      const poll = () => {
        if (getCached(id) || Date.now() - start > 15000) {
          setExpandLoading(false);
          setCacheKeys(new Set(Object.keys(contentCacheRef.current)));
          return;
        }
        setTimeout(poll, 200);
      };
      poll();
      return;
    }

    // Fallback: fetch on demand
    setExpandLoading(true);
    try {
      const content = await fetchThreadContent(id);
      contentCacheRef.current[id] = content;
      setCacheKeys((prev) => new Set(prev).add(id));
    } catch {
      // ignore
    } finally {
      setExpandLoading(false);
    }
  };

  const handleStart = async () => {
    if (selected.size === 0) return;
    setStarting(true);

    const threadIds = Array.from(selected);
    const subjects: Record<string, string> = {};
    for (const t of threads) {
      if (selected.has(t.id)) subjects[t.id] = t.subject;
    }

    // Fire-and-forget: batch pre-fetch in background (don't block UI)
    const uncached = threadIds.filter((id) => !getCached(id));
    if (uncached.length > 0) {
      batchFetchThreads(uncached).then((results) => {
        for (const [id, content] of Object.entries(results)) {
          if (content) {
            contentCacheRef.current[id] = content;
          }
        }
        setCacheKeys(new Set(Object.keys(contentCacheRef.current)));
      }).catch(() => {});
    }

    try {
      // Create run — don't wait for pre-fetch
      const run = await createRun(threadIds, subjects);
      onRunCreated(run, { ...contentCacheRef.current });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Unknown error";
      alert(`Failed to deploy swarm: ${msg}`);
    } finally {
      setStarting(false);
    }
  };

  const filteredThreads = threads.filter((t) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      t.subject.toLowerCase().includes(q) ||
      t.sender.toLowerCase().includes(q) ||
      t.snippet.toLowerCase().includes(q)
    );
  });

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl bg-surface border border-border overflow-hidden hover-glow"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-surface-2">
        <div className="flex items-center gap-2">
          <Mail className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">Inbox</span>
          {threads.length > 0 && (
            <span className="text-[10px] text-muted font-mono bg-honey-glow text-primary px-2 py-0.5 rounded-full border border-primary/20">
              {threads.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={loadThreads}
            disabled={loading}
            className="p-1.5 rounded-lg text-muted hover:text-primary hover:bg-honey-glow transition-all"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          <button
            onClick={selected.size === filteredThreads.length ? deselectAll : selectAll}
            className="text-xs text-muted hover:text-primary transition-colors px-2 py-1 rounded-lg hover:bg-honey-glow"
          >
            {selected.size === filteredThreads.length && filteredThreads.length > 0
              ? "Deselect All"
              : "Select All"}
          </button>
        </div>
      </div>

      {/* Search */}
      <div className="px-4 py-2 border-b border-border">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted" />
          <input
            type="text"
            placeholder="Search emails..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-8 pr-3 py-1.5 text-sm rounded-lg bg-surface-2 border border-border focus:border-primary focus:ring-1 focus:ring-primary/20 outline-none text-foreground transition-all"
          />
        </div>
      </div>

      {/* Thread list */}
      <div className="max-h-[400px] overflow-y-auto">
        {loading && threads.length === 0 ? (
          <div className="flex items-center justify-center py-12 text-muted">
            <Loader2 className="w-5 h-5 animate-spin text-primary mr-2" />
            <span className="text-sm">Loading inbox...</span>
          </div>
        ) : error ? (
          <div className="text-center py-12 text-sm text-error px-4">
            {error}
          </div>
        ) : filteredThreads.length === 0 ? (
          <div className="text-center py-12 text-sm text-muted">
            {searchQuery ? "No emails match your search" : "No emails found"}
          </div>
        ) : (
          filteredThreads.map((thread, i) => {
            const cachedContent = getCached(thread.id);
            return (
              <motion.div
                key={thread.id}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.15 }}
              >
                <div
                  className={`flex items-start gap-3 px-4 py-2.5 border-b border-border/50 cursor-pointer transition-all duration-200 ${
                    selected.has(thread.id)
                      ? "bg-honey-glow border-l-2 border-l-primary"
                      : "hover:bg-surface-2"
                  }`}
                >
                  {/* Checkbox */}
                  <button
                    onClick={() => toggleSelect(thread.id)}
                    className="mt-0.5 flex-shrink-0 text-muted hover:text-primary transition-colors"
                  >
                    {selected.has(thread.id) ? (
                      <CheckSquare className="w-4 h-4 text-primary" />
                    ) : (
                      <Square className="w-4 h-4" />
                    )}
                  </button>

                  {/* Content */}
                  <div className="flex-1 min-w-0" onClick={() => toggleExpand(thread.id)}>
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className={`text-sm truncate ${thread.unread ? "font-semibold text-foreground" : "font-normal text-foreground/80"}`}>
                        {thread.sender}
                      </span>
                      <span className="text-xs text-muted flex-shrink-0">{thread.date}</span>
                      {thread.unread && (
                        <span className="w-1.5 h-1.5 rounded-full bg-honey flex-shrink-0" />
                      )}
                    </div>
                    <div className={`text-sm truncate ${thread.unread ? "font-medium text-foreground" : "text-foreground/80"}`}>
                      {thread.subject}
                    </div>
                    <div className="text-xs text-muted truncate mt-0.5">
                      {thread.snippet}
                    </div>
                  </div>

                  {/* Expand indicator */}
                  <button
                    onClick={() => toggleExpand(thread.id)}
                    className="mt-1 text-muted hover:text-primary transition-colors flex-shrink-0"
                  >
                    {expanded === thread.id ? (
                      <ChevronDown className="w-4 h-4" />
                    ) : (
                      <ChevronRight className="w-4 h-4" />
                    )}
                  </button>
                </div>

                {/* Expanded content */}
                <AnimatePresence>
                  {expanded === thread.id && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: "auto", opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                      className="overflow-hidden"
                    >
                      <div className="px-4 py-3 bg-surface-2 border-b border-border/50 border-l-2 border-l-primary/30">
                        {expandLoading && !cachedContent ? (
                          <div className="flex items-center text-muted text-xs gap-2">
                            <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />
                            Loading content...
                          </div>
                        ) : cachedContent ? (
                          <div className="space-y-3">
                            {cachedContent.messages.map((msg, mi) => (
                              <div key={mi} className="text-xs">
                                <div className="font-medium text-foreground mb-0.5">
                                  {msg.from || "Unknown"}{" "}
                                  <span className="text-muted font-normal">{msg.date}</span>
                                </div>
                                <div className="text-muted whitespace-pre-wrap leading-relaxed max-h-32 overflow-y-auto">
                                  {msg.body.slice(0, 500)}
                                  {msg.body.length > 500 && "..."}
                                </div>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <div className="text-xs text-muted">Could not load content</div>
                        )}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.div>
            );
          })
        )}
      </div>

      {/* Footer — Deploy button */}
      <div className="px-4 py-3 border-t border-border bg-surface-2 flex items-center justify-between">
        <span className="text-xs text-muted">
          {selected.size} email{selected.size !== 1 ? "s" : ""} selected
        </span>
        <motion.button
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          onClick={handleStart}
          disabled={selected.size === 0 || starting || disabled}
          className="flex items-center gap-2 px-5 py-2 rounded-lg bg-gradient-to-r from-primary to-primary-light hover:from-primary-light hover:to-honey disabled:opacity-40 disabled:cursor-not-allowed transition-all text-background font-semibold text-sm shadow-lg shadow-primary/20"
        >
          {starting ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Hexagon className="w-4 h-4" />
          )}
          {starting ? "Deploying..." : `Deploy Swarm (${selected.size})`}
        </motion.button>
      </div>
    </motion.div>
  );
}
