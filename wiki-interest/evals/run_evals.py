#!/usr/bin/env python3
"""Run the evals in evals.json through Claude Code in headless mode (claude -p).

Each eval runs in a fresh session, in a throwaway project folder that either contains the skill
(.claude/skills/wiki-interest -> this skill) or not (baseline). When the agent ends a turn with a
question, the next scripted reply from evals.json is sent with --resume.

    python3 evals/run_evals.py --workspace ../wiki-interest-workspace --iteration 1
    python3 evals/run_evals.py --workspace ../wiki-interest-workspace --iteration 1 --ids 2 --configs with_skill

Outputs per run: <workspace>/iteration-N/eval-<name>/<config>/{trace.jsonl, transcript.md, timing.json}
"""

import argparse
import json
import subprocess
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
ALLOWED_TOOLS = ["Bash", "Read", "Write", "Edit", "Glob", "Grep", "Skill"]  # web tools are denied


def project_dir(workspace, config):
    """A clean project folder per configuration; with_skill links the skill in.

    It lives outside the repository on purpose: an agent without the skill must not be able to find
    the skill's scripts by browsing parent folders (that contaminated the first baseline).
    """
    d = Path(tempfile.gettempdir()) / "wiki-interest-evals" / f"project-{config}"
    skills = d / ".claude" / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    link = skills / "wiki-interest"
    if config == "with_skill" and not link.exists():
        link.symlink_to(SKILL_DIR)
    if config == "without_skill" and link.is_symlink():
        link.unlink()
    return d


def claude(prompt, cwd, model, session_id=None, budget=1.5, timeout=1200):
    cmd = ["claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
           "--allowedTools", *ALLOWED_TOOLS, "--strict-mcp-config", "--setting-sources", "project,local",
           "--max-budget-usd", str(budget)]
    if session_id:
        cmd += ["--resume", session_id]
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    events = []
    for line in proc.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    return events, proc.stderr


def final_result(events):
    return next((e for e in reversed(events) if e.get("type") == "result"), {})


def transcript(turns):
    out = []
    for i, (user_msg, events) in enumerate(turns, 1):
        out.append(f"## Turn {i}\n\n**User:** {user_msg}\n")
        for e in events:
            if e.get("type") == "assistant":
                for c in e["message"].get("content", []):
                    if c.get("type") == "text" and c["text"].strip():
                        out.append(f"**Assistant:** {c['text'].strip()}\n")
                    elif c.get("type") == "tool_use":
                        arg = c["input"].get("command") or json.dumps(c["input"], ensure_ascii=False)
                        out.append(f"**Tool `{c['name']}`:** `{arg[:600]}`\n")
            elif e.get("type") == "user":
                content = e.get("message", {}).get("content", [])
                for c in content if isinstance(content, list) else []:
                    if c.get("type") == "tool_result":
                        text = c.get("content")
                        if isinstance(text, list):
                            text = " ".join(x.get("text", "") for x in text if isinstance(x, dict))
                        flag = " (error)" if c.get("is_error") else ""
                        out.append(f"<details><summary>result{flag}</summary>\n\n```\n{str(text)[:1500]}\n```\n</details>\n")
        r = final_result(events)
        out.append(f"_turn: {r.get('num_turns')} steps, {r.get('duration_ms')} ms, ${r.get('total_cost_usd')}_\n")
    return "\n".join(out)


def run_eval(ev, config, workspace, out_root, model, run=1):
    cwd = project_dir(workspace, config)
    out = out_root / f"eval-{ev['name']}" / (config if run == 1 else f"{config}-run{run}")
    out.mkdir(parents=True, exist_ok=True)
    turns, all_events, session, replies = [], [], None, list(ev.get("replies", []))
    message = ev["prompt"]
    started = time.monotonic()
    while True:
        events, stderr = claude(message, cwd, model, session)
        if stderr.strip():
            (out / "stderr.txt").open("a").write(stderr)
        turns.append((message, events))
        all_events += [{"_turn": len(turns), **e} for e in events]
        result = final_result(events)
        session = result.get("session_id") or session
        answer = result.get("result") or ""
        if not replies or "?" not in answer[-400:]:  # continue only if the agent asked something
            break
        message = replies.pop(0)

    (out / "trace.jsonl").write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in all_events))
    (out / "transcript.md").write_text(f"# {ev['name']} · {config} · {model}\n\n" + transcript(turns))
    results = [final_result(ev_) for _, ev_ in turns]
    timing = {
        "turns": len(turns),
        "duration_ms": round((time.monotonic() - started) * 1000),
        "total_cost_usd": round(sum(r.get("total_cost_usd") or 0 for r in results), 4),
        "total_tokens": sum((r.get("usage") or {}).get(k, 0) for r in results
                            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                      "cache_creation_input_tokens")),
    }
    (out / "timing.json").write_text(json.dumps(timing, indent=2))
    return out, timing


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", required=True)
    p.add_argument("--iteration", type=int, required=True)
    p.add_argument("--ids", help="Comma-separated eval ids (default: all).")
    p.add_argument("--configs", default="with_skill,without_skill")
    p.add_argument("--model", default="haiku")
    p.add_argument("--repeat", type=int, default=1, help="Runs per eval and config (model output varies).")
    args = p.parse_args()

    evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text())["evals"]
    if args.ids:
        wanted = {int(i) for i in args.ids.split(",")}
        evals = [e for e in evals if e["id"] in wanted]
    workspace = Path(args.workspace).resolve()
    out_root = workspace / f"iteration-{args.iteration}"
    for ev in evals:
        for config in args.configs.split(","):
            for run in range(1, args.repeat + 1):
                out, timing = run_eval(ev, config, workspace, out_root, args.model, run)
                print(json.dumps({"eval": ev["name"], "config": config, "run": run, **timing}), flush=True)


if __name__ == "__main__":
    main()
