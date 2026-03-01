#!/bin/bash
# Start the Parallel backend server
cd "$(dirname "$0")"
.venv/bin/uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
