#!/bin/bash

if [ -z "$HOST" ]; then
    export HOST="0.0.0.0"
fi
if [ -z "$PORT" ]; then
    export PORT="7860"
fi

if [ "$KH_SSO_ENABLED" = "true" ]; then
    echo "KH_SSO_ENABLED is true. Launching SecureRAG with SSO..."
    KH_SSO_ENABLED=true .venv/bin/uvicorn sso_app:app --host "$HOST" --port "$PORT"
else
    if command -v ollama >/dev/null 2>&1; then
        ollama serve &
    fi
    KH_FEATURE_USER_MANAGEMENT=false .venv/bin/uvicorn app:app --host "$HOST" --port "$PORT"
fi
