"use client";

import { useEffect } from "react";
import { motion } from "framer-motion";
import { CheckCircle2 } from "lucide-react";

export default function AuthCallbackPage() {
  useEffect(() => {
    // This page is no longer needed with browser-based auth.
    // Redirect to dashboard.
    setTimeout(() => {
      window.location.href = "/";
    }, 1000);
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center">
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        className="text-center space-y-4"
      >
        <CheckCircle2 className="w-12 h-12 text-success mx-auto" />
        <p className="text-muted">Redirecting to dashboard...</p>
      </motion.div>
    </div>
  );
}
