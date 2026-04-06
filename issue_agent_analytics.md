## Problem
Das Plugin agent-analytics nutzt subprocess.run (journalctl), hat aber permissions: [] statt system.exec deklariert.

## Impact
Das Plugin-Manifest ist inkonsistent - ein Permission-System kann Subprocess-Aufrufe nicht korrekt prüfen/blockieren.

## Evidence
plugins/agent-analytics/plugin.yaml: permissions: []
plugins/agent-analytics/plugin.py:16,45: import subprocess + subprocess.run(["journalctl", ...]

## Expected Behavior
permissions:
  - system.exec

## Fix
Bereits in commit 1b469d5 behoben.
