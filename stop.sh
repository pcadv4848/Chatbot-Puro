#!/usr/bin/env bash
pkill -f "uvicorn src.main:app" 2>/dev/null || true
pkill -f "npm start" 2>/dev/null || true
pkill -f "openwa" 2>/dev/null || true
echo "Parou"
