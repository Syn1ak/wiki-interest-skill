#!/usr/bin/env python3
"""Grade eval runs from their traces: check each assertion in evals.json with evidence.

    python3 evals/grade.py --workspace ../wiki-interest-workspace --iteration 1

Writes grading.json per run and benchmark.json + summary.md per iteration. Checks are mechanical
(commands, arguments, statuses, number provenance) plus keyword heuristics for answer content;
the transcripts should still be read by a human.
"""

import argparse
import json
import re
import shlex
import statistics
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
NUMBER_RE = re.compile(r"[-+−]?\d{1,3}(?:[   ]\d{3})+(?:[.,]\d+)?|[-+−]?\d+(?:[.,]\d+)?")
IGNORE_RE = re.compile(r"\bQ\d+\b|\b(?:19|20)\d{2}(?:[-–./]\d{1,2}){0,2}\b|https?://\S+|`[^`]*`|\b[\w-]+\.(?:pdf|png|json)\b")
SCALE_RE = re.compile(r"\s*(тис|тыс|k\b|K\b|thousand|млн|million|M\b)")
DATA_STATUSES = {"ok", "partial"}


# --- Trace parsing ---------------------------------------------------------------------------

def load_run(run_dir):
    events = [json.loads(l) for l in (run_dir / "trace.jsonl").read_text().splitlines() if l.strip()]
    calls, results = [], {}
    for e in events:
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        for c in content:
            if e["type"] == "assistant" and c.get("type") == "tool_use":
                calls.append({"id": c["id"], "turn": e["_turn"], "name": c["name"], "input": c["input"]})
            elif e["type"] == "user" and c.get("type") == "tool_result":
                text = c.get("content")
                if isinstance(text, list):
                    text = " ".join(x.get("text", "") for x in text if isinstance(x, dict))
                results[c["tool_use_id"]] = str(text or "")
    # A turn can emit several result events (e.g. after background tasks); the last one is the answer.
    last = {}
    for e in events:
        if e.get("type") == "result":
            last[e["_turn"]] = e.get("result") or ""
    answers = sorted(last.items())
    wi = []
    for c in calls:
        cmd = c["input"].get("command", "") if c["name"] == "Bash" else ""
        m = re.search(r"wi\.py\s+(\w+)(.*)", cmd, re.S)
        if not m:
            continue
        try:
            argv = shlex.split(m.group(2).split("|")[0].split("&&")[0])
        except ValueError:
            argv = m.group(2).split()
        out = results.get(c["id"], "")
        try:  # the harness may append notes after the JSON document
            parsed = json.JSONDecoder().raw_decode(out[out.index("{"):])[0] if "{" in out else {}
        except ValueError:
            parsed = {}
        wi.append({"turn": c["turn"], "sub": m.group(1), "args": argv, "out": parsed, "raw": cmd})
    return {"calls": calls, "wi": wi, "answers": answers, "results": results}


def arg(call, name):
    a = call["args"]
    return a[a.index(name) + 1] if name in a and a.index(name) + 1 < len(a) else None


def data_calls(run):
    """Calls that actually downloaded/analysed data."""
    return [c for c in run["wi"] if c["sub"] in ("study", "fetch") and c["out"].get("status") in DATA_STATUSES]


def langs_of(call):
    v = arg(call, "--langs")
    return {x.strip() for x in v.split(",")} if v else set()


# --- Number provenance -------------------------------------------------------------------------

def allowed_numbers(run):
    values = set(range(0, 13)) | {24, 36, 60}  # counts, list numbering, months, common periods

    def walk(x, key=""):
        if isinstance(x, bool):
            return
        if isinstance(x, (int, float)):
            values.add(abs(x))
            if abs(x) <= 10:
                values.add(abs(x) * 100)
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, k)
        elif isinstance(x, list):
            for v in x:
                walk(v, key)
        elif isinstance(x, str):
            for t in re.findall(r"\d+(?:\.\d+)?", IGNORE_RE.sub(" ", x)):
                values.add(float(t))

    for c in run["wi"]:
        walk(c["out"])
    # Tool output read in other ways (e.g. a background task's output file) and every analysis file
    # the run referenced also count as tool output.
    blobs = [json.dumps(c["input"], ensure_ascii=False) for c in run["calls"]] + list(run["results"].values())
    for text in run["results"].values():
        if "{" in text:
            try:
                walk(json.JSONDecoder().raw_decode(text[text.index("{"):])[0])
            except ValueError:
                pass
    for path in {m for b in blobs for m in re.findall(r"[\w./-]+\.analysis\.json", b)}:
        p = Path(path) if Path(path).is_absolute() else SKILL_DIR / path
        if p.exists():
            walk(json.loads(p.read_text()).get("results", {}))
    return values


def unverified_numbers(text, allowed):
    bad = []
    clean = IGNORE_RE.sub(" ", text)
    for m in NUMBER_RE.finditer(clean):
        token = m.group(0)
        t = token.replace("−", "-").replace(" ", "").replace(" ", "").replace(" ", "").lstrip("+-")
        if re.fullmatch(r"\d+,\d{3}", t):
            t = t.replace(",", "")
        n = float(t.replace(",", "."))
        decimals = len(re.split(r"[.,]", t)[1]) if re.search(r"[.,]\d{1,2}$", t) else 0
        tol = 0.5 * 10 ** -decimals + 1e-9
        if re.search(r"(~|≈|близько|приблизно|about|around)\s*$", clean[max(0, m.start() - 12):m.start()]):
            tol = max(tol, abs(n) * 0.01)  # the text itself says the number is approximate
        scale = SCALE_RE.match(clean[m.end():m.end() + 12])
        if scale:
            mult = 1e6 if scale.group(1) in ("млн", "million", "M") else 1e3
            n, tol = n * mult, tol * mult
        if not any(abs(n - a) <= tol for a in allowed):
            bad.append(token.strip())
    return bad


# --- Assertions ------------------------------------------------------------------------------------

def check(assertion, run):
    a = assertion.lower()
    answers = run["answers"]
    all_text = "\n".join(t for _, t in answers)
    first = answers[0][1] if answers else ""
    dcalls = data_calls(run)
    turn1_data = [c for c in dcalls if c["turn"] == 1]

    if a == "uses the skill's scripts":
        ok = bool(run["wi"])
        return ok, f"{len(run['wi'])} wi.py calls" if ok else "no wi.py call"
    if a == "uses study for the analysis":
        s = [c for c in run["wi"] if c["sub"] == "study"]
        return bool(s), "; ".join(c["raw"][-120:] for c in s[:3]) or "no study call"
    first_turn_only = a.startswith("in the first answer, ")
    if first_turn_only:
        a = a.removeprefix("in the first answer, ")
        dcalls = turn1_data
    if a.startswith("requests"):
        m = re.search(r"languages? (?:the user named )?\(?([a-z, ]+(?: and [a-z]+)?)\)?$", a)
        expected = set(re.split(r",\s*|\s+and\s+", m.group(1).strip())) if m else set()
        used = set().union(*(langs_of(c) for c in dcalls)) if dcalls else set()
        return used == expected, f"langs used: {sorted(used)}; expected {sorted(expected)}"
    if a == "period is the last 24 months":
        bad = [c["raw"][-100:] for c in dcalls if arg(c, "--start") or arg(c, "--months") not in (None, "24")]
        return bool(dcalls) and not bad, "; ".join(bad) or "default 24 months"
    if a.startswith("tells the user that polish has no article"):
        pat = (r"(польськ\w*|polish|\bpl\b)[^.\n]{0,120}(нема|відсутн|no (?:dedicated )?article|n[o']t have|not found)"
               r"|(нема|відсутн|no article|not)[^.\n]{0,120}(польськ|polish)")
        hit = re.search(pat, first, re.I | re.S)
        return bool(hit), hit.group(0)[:160] if hit else "turn-1 answer does not say Polish has no article"
    if a.startswith("does not use a proxy article before"):
        early = [c["raw"][-120:] for c in run["wi"] if c["turn"] == 1 and "--article" in c["args"]]
        return not early, "; ".join(early) or "no --article in turn 1"
    if a == "answers in ukrainian in every turn":
        def cyr_share(t):
            letters = re.findall(r"[^\W\d_]", IGNORE_RE.sub(" ", t))
            return sum("\u0400" <= ch <= "\u04ff" for ch in letters) / max(1, len(letters))
        shares = [round(cyr_share(t), 2) for _, t in answers]
        return all(x > 0.6 for x in shares), f"Cyrillic share per turn: {shares}"
    if a == "states confidence":
        hit = re.search(r"довір\w*|надійн\w*|confidence", all_text, re.I)
        return bool(hit), hit.group(0) if hit else "no confidence statement"
    if a.startswith("says interest is falling"):
        hit = re.search(r"пада\w*|знизи\w*|знижу\w*|спад\w*|falling|declin\w*", first, re.I)
        return bool(hit), hit.group(0) if hit else "no 'falling' in the answer"
    if a.startswith("mentions seasonality"):
        hit = re.search(r"вересн\w*|september|навчальн\w* р\w*|school", all_text, re.I)
        return bool(hit), hit.group(0) if hit else "no seasonality mention"
    if a.startswith("asks which languages") or a.startswith("asks which meaning"):
        asked = "?" in first[-600:]
        return asked and not turn1_data, (f"asked: {asked}; data calls in turn 1: {len(turn1_data)}; "
                                          f"end of turn 1: …{first[-160:]!r}")
    if a.startswith("after the answer, reruns with --qid"):
        q = [c["raw"][-100:] for c in dcalls if c["turn"] >= 2 and "--qid" in c["args"]]
        return bool(q), "; ".join(q) or "no --qid data call after the user's answer"
    if a.startswith("runs report"):
        reps = [c for c in run["wi"] if c["sub"] == "report"]
        last = reps[-1]["out"].get("status") if reps else None
        return last == "ok", f"{len(reps)} report calls, last status {last}"
    if a.startswith("report content lists"):
        blobs = [json.dumps(c["input"], ensure_ascii=False) for c in run["calls"] if c["name"] in ("Write", "Bash")]
        hits = [b for b in blobs if re.search(r'\\?"limitations\\?"\s*:\s*\[\s*\\?"', b)]
        return bool(hits), "limitations field found in report content" if hits else "no limitations in report content"
    if a.startswith("recommends which audiences"):
        hit = re.search(r"(рекоменд|дослід\w* наступн|варто дослід|next)", all_text, re.I)
        return bool(hit), hit.group(0) if hit else "no recommendation wording"
    if a.startswith("every number"):
        bad = unverified_numbers(all_text, allowed_numbers(run))
        return not bad, f"unverified: {bad[:12]}" if bad else "all numbers found in tool output"
    return None, "no automatic check"


def grade_run(run_dir, ev):
    run = load_run(run_dir)
    rows = []
    for text in ev["assertions"]:
        passed, evidence = check(text, run)
        rows.append({"text": text, "passed": passed, "evidence": evidence})
    graded = [r for r in rows if r["passed"] is not None]
    n_pass = sum(r["passed"] for r in graded)
    out = {"assertion_results": rows,
           "summary": {"passed": n_pass, "failed": len(graded) - n_pass, "total": len(graded),
                       "pass_rate": round(n_pass / len(graded), 3) if graded else None}}
    (run_dir / "grading.json").write_text(json.dumps(out, ensure_ascii=False, indent=2))
    return out


def stats(xs):
    return {"mean": round(statistics.mean(xs), 3), "stddev": round(statistics.pstdev(xs), 3)} if xs else None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workspace", required=True)
    p.add_argument("--iteration", type=int, required=True)
    args = p.parse_args()
    root = Path(args.workspace).resolve() / f"iteration-{args.iteration}"
    evals = json.loads((SKILL_DIR / "evals" / "evals.json").read_text())["evals"]

    per_config, lines = {}, [f"# Iteration {args.iteration}\n", "| Eval | Config | Passed | Time, s | Cost, $ |",
                              "|---|---|---|---|---|"]
    details = []
    for ev in evals:
        for run_dir in sorted((root / f"eval-{ev['name']}").glob("*")):
            if not (run_dir / "trace.jsonl").exists():
                continue
            g = grade_run(run_dir, ev)
            t = json.loads((run_dir / "timing.json").read_text())
            cfg = run_dir.name.split("-run")[0]  # repeated runs count towards the same configuration
            per_config.setdefault(cfg, {"pass_rate": [], "time_seconds": [], "tokens": [], "cost": []})
            per_config[cfg]["pass_rate"].append(g["summary"]["pass_rate"] or 0)
            per_config[cfg]["time_seconds"].append(t["duration_ms"] / 1000)
            per_config[cfg]["tokens"].append(t["total_tokens"])
            per_config[cfg]["cost"].append(t["total_cost_usd"])
            s = g["summary"]
            lines.append(f"| {ev['name']} | {run_dir.name} | {s['passed']}/{s['total']} | {t['duration_ms'] / 1000:.0f} | "
                         f"{t['total_cost_usd']:.3f} |")
            details.append(f"\n## {ev['name']} · {run_dir.name}\n")
            details += [f"- {'PASS' if r['passed'] else 'FAIL' if r['passed'] is False else 'n/a '} "
                        f"{r['text']}: {r['evidence']}" for r in g["assertion_results"]]

    bench = {"run_summary": {cfg: {k: stats(v) for k, v in d.items()} for cfg, d in per_config.items()}}
    if {"with_skill", "without_skill"} <= set(per_config):
        w, wo = bench["run_summary"]["with_skill"], bench["run_summary"]["without_skill"]
        bench["run_summary"]["delta"] = {k: round(w[k]["mean"] - wo[k]["mean"], 3) for k in w}
    (root / "benchmark.json").write_text(json.dumps(bench, indent=2))
    (root / "summary.md").write_text("\n".join(lines + details) + "\n")
    print("\n".join(lines))
    print(json.dumps(bench, indent=1))


if __name__ == "__main__":
    main()
