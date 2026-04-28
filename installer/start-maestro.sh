#!/usr/bin/env bash
mkdir -p /tmp/.maestro
touch /tmp/.maestro/audit.jsonl /tmp/.maestro/decisions.jsonl
cd /tmp
exec /usr/bin/node /usr/bin/maestro-workflow-mcp --http --port 3004
