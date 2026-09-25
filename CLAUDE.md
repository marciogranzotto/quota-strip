# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The shared project guide lives in `AGENTS.md`, so Claude Code, Codex and other agents read the same instructions. Edit that file for project facts, commands, architecture, invariants, product decisions and deployment notes. Keep only Claude Code–specific guidance here.

@AGENTS.md

## Claude Code specifics

- Where `AGENTS.md` refers to the "monitor MCP" / `monitor_wait` for asynchronous work, use Claude Code's `Monitor` tool, or `run_in_background` Bash commands, which notify on exit. Don't poll with repeated shell calls. The SD-card flashing exception still applies: let the user report completion themselves.
