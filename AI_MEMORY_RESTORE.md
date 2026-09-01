# AI Context & Memory Restoration Guide

If you have switched to a new Gemini account, the AI will start with a blank conversation history and will not immediately remember the context of this project, the previous plans made, or the split-brain bug discussions.

However, the complete memory of the previous session is safely stored locally on your machine. 

## How to Restore Memory

To instantly get the AI back up to speed with the exact conversation history and context from the previous account, simply copy and paste the following prompt into your **new chat window**:

```text
Please read the conversation transcript located at:
C:\Users\User\.gemini\antigravity-ide\brain\d23d3dfb-ae4b-4cd7-8a3c-bf43be851031\.system_generated\logs\transcript.jsonl

This file contains our complete previous conversation history. Please review it to regain full context on the ECO-Fi Plastic Bottle Vending Machine project, the implementation plans we've created, and the split-brain issues we discovered. Once you've read it, let me know that your memory is restored and we can continue working!
```

## Other Important Files to Reference
If you ever lose the transcript or start a completely fresh environment, you can also point the AI to these key files which contain our saved work:
- **Implementation Plan:** `d:\PROJECTS_IO\Plastic-Bottle-Vending-Machine\plans\eco_fi_client_handling_plan.md` (Contains the detailed plan to fix the `portal.py` and firewall `daemon.py` split-brain bug).
- **Unpacked OS Image:** `d:\PROJECTS_IO\Plastic-Bottle-Vending-Machine\resources\unpacked_img` (Contains the extracted contents of the original PisoFi image).
