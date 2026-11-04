"""Mutation check: each seeded defect must make the test suite fail.

Every mutation disables one refusal or one rule of the protocol, or loosens one bound. The
script copies the package and the tests to a temporary directory, applies one mutation, and
runs pytest against the copy. A mutation the tests do not catch is a survivor, and the
script exits 1.

    python tools/mutation_check.py [--only NAME]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG = "src/sealed_panel"
REC, AGG, SPEC, CFG, BRIEF = (
    f"{PKG}/{m}.py" for m in ("record", "aggregate", "spec", "config", "brief")
)
DEFAULTS = f"{PKG}/defaults.toml"
CFG_NORM = '__import__("sealed_panel.text", fromlist=["normalise"]).normalise'

# name: (file under the repo root, exact text, replacement)
MUTATIONS: dict[str, tuple[str, str, str]] = {
    # Calibration of each judge.
    "control check off": (REC, 'if mean_ctrl > spec["control_max"]:', "if False:"),
    "control bound inclusive": (
        REC,
        'if mean_ctrl > spec["control_max"]:',
        'if mean_ctrl >= spec["control_max"]:',
    ),
    "known-good check off": (REC, 'statistics.mean(good.values()) < spec["good_min"]', "False"),
    "known-good bound inclusive": (
        REC,
        'statistics.mean(good.values()) < spec["good_min"]',
        'statistics.mean(good.values()) <= spec["good_min"]',
    ),
    # Blind briefs.
    "no shuffle": (BRIEF, "rng.shuffle(order)", "pass"),
    "previous dropped": (BRIEF, 'roles.append(("previous", read_text(prev_path)))', "pass"),
    "item tag allowed": (BRIEF, "if FENCE.search(text):", "if False:"),
    "round zero allowed": (BRIEF, "if rnd < 1:", "if False:"),
    "brief twice": (BRIEF, 'if rdir.exists() and any(rdir.glob("brief_*.md")):', "if False:"),
    # Quotes and reply cells.
    "quote check off": (REC, "if not quote_found(", "if False and not quote_found("),
    "tiny fragments allowed": (
        f"{PKG}/text.py",
        "if any(len(p) < cfg.min_fragment_chars for p in parts):",
        "if False:",
    ),
    "fragment order ignored": (f"{PKG}/text.py", "pos = i + len(p)", "pos = 0"),
    "smart quotes not normalised": (DEFAULTS, '"\\u201c" = "\\""\n', ""),
    "boolean score accepted": (
        REC,
        "if isinstance(s, bool) or not isinstance(s, int) or",
        "if not isinstance(s, int) or",
    ),
    "unknown severity accepted": (REC, "if sev not in cfg.severities:", "if False:"),
    "empty reasoning accepted": (REC, 'if not str(d.get("reasoning", "")).strip():', "if False:"),
    "severity cap off": (REC, "if s > caps[sev]:", "if False:"),
    "blocking cap 4": (DEFAULTS, "blocking = 3,", "blocking = 4,"),
    "bad-quote share loosened": (DEFAULTS, 'max_bad_share = "1/3"', 'max_bad_share = "1/2"'),
    "short quotes accepted": (DEFAULTS, "min_chars = 12", "min_chars = 8"),
    "one-quote flag off": (
        REC,
        '"one_quote": len(quotes) >= 2 and len(set(quotes)) == 1,',
        '"one_quote": False,',
    ),
    # Recording: every way to re-roll a reply.
    "missing judge unchecked": (REC, "if meta is None:", "if False:"),
    "agent reuse allowed": (REC, "if agent_id in used_agent_ids(run):", "if False:"),
    "empty agent id accepted": (REC, "if not agent_id.strip():", "if False:"),
    "reply reuse allowed": (REC, "if digest in used_reply_hashes(run):", "if False:"),
    "second valid reply allowed": (REC, 'if any(r["ok"] for r in prior):', "if False:"),
    "replacements uncapped": (
        REC,
        "    if len(real) > cfg.max_replacements:\n        raise EvalError(\n",
        "    if False:\n        raise EvalError(\n",
    ),
    "late judge allowed": (
        REC,
        '    if (rdir / "result.json").exists():\n'
        '        raise EvalError("this round is already aggregated: a late',
        '    if False:\n        raise EvalError("this round is already aggregated: a late',
    ),
    "whole-brief check off": (REC, "if brief not in prompt:", "if False:"),
    "prompt extras unrecorded": (
        REC,
        "extra_chars = len(prompt) - len(brief)",
        "extra_chars = 0",
    ),
    "void without the brief id": (
        REC,
        'if meta["nonce"] not in read_text(transcript):',
        "if False:",
    ),
    "void without any brief id": (REC, 'if not meta.get("nonce"):', "if False:"),
    "a pasted reply can be voided": (REC, "if pasted and replied:", "if False:"),
    "only the last reply read for a void": (
        REC,
        "replies = assistant_texts(transcript, cfg) + [m for m in handed if m]",
        "replies = assistant_texts(transcript, cfg)[-1:]",
    ),
    "a malformed handback can be voided": (
        REC,
        "replied = malformed or any(r.strip() for r in replies)",
        "replied = any(r.strip() for r in replies)",
    ),
    "void label hides a reply": (
        REC,
        '"replied": replied,',
        '"replied": False,',
    ),
    "voids uncapped": (REC, "if len(voids) >= MAX_VOIDS:", "if False:"),
    "voids count as real attempts": (
        REC,
        'real = [r for r in prior if not r.get("void")]',
        "real = list(prior)",
    ),
    "handback ignored": (
        REC,
        "    if handed:\n        last = handed[-1]",
        "    if False:\n        last = handed[-1]",
    ),
    "refused handback read as delivered": (
        REC,
        "            if not include_refused and call_id is not None and call_id in refused:",
        "            if False:",
    ),
    "refused error without an id skips id-less calls": (
        REC,
        "if not include_refused and call_id is not None and call_id in refused:",
        "if not include_refused and call_id in refused:",
    ),
    "refused handback ignored for a void": (
        REC,
        "handed = handback_messages(transcript, cfg, include_refused=True)",
        "handed = handback_messages(transcript, cfg)",
    ),
    "void cap boundary": (REC, "if len(voids) >= MAX_VOIDS:", "if len(voids) > MAX_VOIDS:"),
    "refused verdict not read when nothing was delivered": (
        REC,
        "    refused = handback_messages(transcript, cfg, include_refused=True)\n    if refused:",
        "    refused = handback_messages(transcript, cfg, include_refused=True)\n    if False:",
    ),
    "an earlier malformed refusal hides the last": (
        REC,
        "        if refused[-1] is None:\n            raise MalformedReply(",
        "        if None in refused:\n            raise MalformedReply(",
    ),
    "malformed refused verdict read as text": (
        REC,
        "        if refused[-1] is None:\n            raise MalformedReply(",
        "        if False:\n            raise MalformedReply(",
    ),
    "empty handback tool not skipped": (
        REC,
        "    if not tool:\n        return []",
        "    if False:\n        return []",
    ),
    "pasted reply on a peeked seat": (
        REC,
        'if source == "output" and any(r.get("void") and r.get("replied") for r in prior):',
        "if False:",
    ),
    "malformed handback read as text": (
        REC,
        "        if last is None:\n            raise MalformedReply(",
        "        if False:\n            raise MalformedReply(",
    ),
    "measure flag overrides threshold": (
        REC,
        "        if passed is not None:\n            raise EvalError(",
        "        if False:\n            raise EvalError(",
    ),
    "measure after aggregate": (
        REC,
        '    if (rdir / "result.json").exists():\n'
        '        raise EvalError("this round is already aggregated: measure',
        '    if False:\n        raise EvalError("this round is already aggregated: measure',
    ),
    # Aggregation.
    "re-aggregate allowed": (
        AGG,
        '    if (rdir / "result.json").exists():\n'
        '        raise EvalError("this round is already aggregated: a round',
        '    if False:\n        raise EvalError("this round is already aggregated: a round',
    ),
    "one judge enough": (AGG, "if len(pv) < cfg.min_valid_judges:", "if len(pv) < 1:"),
    "per-dimension minimum off": (AGG, "if len(cand) < cfg.min_valid_judges:", "if False:"),
    "silent slot allowed": (AGG, "if silent:", "if False:"),
    "a void fills a seat": (
        AGG,
        'if not any(not r.get("void") for r in by_judge.get(j["judge"], []))',
        'if not by_judge.get(j["judge"])',
    ),
    "voids hidden from the ledger": (
        AGG,
        '            for r in rejected\n            if r.get("void")',
        "            for r in rejected\n            if False",
    ),
    "unrecorded agents ignored": (AGG, "if unrecorded:", "if False:"),
    "sealed panel aggregated unmeasured": (AGG, "if missing:", "if False:"),
    "missing transcript folder crashes": (AGG, "if not folder.is_dir():", "if False:"),
    "suspect jump off": (AGG, 'if e.get("paired_gain", 0) >= SUSPECT_JUMP', "if False"),
    "suspect jump exclusive": (
        AGG,
        'if e.get("paired_gain", 0) >= SUSPECT_JUMP',
        'if e.get("paired_gain", 0) > SUSPECT_JUMP',
    ),
    # Stop rule and sealing.
    "plateau off": (
        f"{PKG}/decide.py",
        'if len(gains) >= n and all(g < last["noise"]',
        'if False and all(g < last["noise"]',
    ),
    "cap off": (f"{PKG}/decide.py", 'if last["round"] >= spec["max_rounds"]:', "if False:"),
    "measured ignored": (
        f"{PKG}/decide.py",
        "instruments_ok = measured_ok(spec, last)",
        "instruments_ok = True",
    ),
    "seal during CONTINUE": (BRIEF, 'if d["decision"] not in STOP_DECISIONS:', "if False:"),
    "loop after seal": (
        BRIEF,
        'if any(r.get("sealed") for r in ledger["rounds"]):',
        "if False:",
    ),
    "same text resubmitted": (
        BRIEF,
        'if text_hash(cand) in {r.get("candidate_hash") for r in loop}:',
        "if False:",
    ),
    # The spec and its freeze.
    "rubric lock off": (SPEC, "if rubric_hash(spec) != read_text(lock).strip():", "if False:"),
    "config lock off": (
        SPEC,
        "if frozen.fingerprint != read_text(lock).strip():",
        "if False:",
    ),
    "declared choice unchecked": (
        SPEC,
        'if not isinstance(c, dict) or not c.get("choice") or not c.get("user_said"):',
        "if False:",
    ),
    "spec injection allowed": (SPEC, "if injection_flags(exposed, cfg):", "if False:"),
    "unknown spec key accepted": (
        SPEC,
        'errors = _unknown(spec, SPEC_KEYS, "spec")',
        "errors = []",
    ),
    "unknown dimension key accepted": (
        SPEC,
        "errors += _unknown(d, DIMENSION_KEYS, where)",
        "errors += []",
    ),
    "facts and facts_file together": (
        SPEC,
        'if "facts" in spec and "facts_file" in spec:',
        "if False:",
    ),
    "missing facts_file accepted": (
        SPEC,
        "if not path.is_file():",
        "if False:",
    ),
    "framings left out of the hash": (
        SPEC,
        "        spec.setdefault(key, value)  # framings included",
        '        if key != "framings":\n'
        "            spec.setdefault(key, value)  # framings included",
    ),
    "non-finite numbers accepted": (SPEC, "and math.isfinite(value)", "and True"),
    "measured min above max accepted": (
        SPEC,
        'and m["min"] > m["max"]:',
        "and False:",
    ),
    "duplicate measured ids accepted": (SPEC, "if len(set(mids)) != len(mids):", "if False:"),
    "measured field types unchecked": (
        SPEC,
        "                errors.append(f\"measured {m.get('id')}: {key} must be a string\")",
        "                pass",
    ),
    "declared choice types unchecked": (
        SPEC,
        '                errors.append(f"declared choice: {key} must be a string")',
        "                pass",
    ),
    "judges per persona unbounded (spec)": (
        SPEC,
        "or not cfg.min_judges_per_persona <= jp <= MAX_JUDGES_PER_PERSONA",
        "or jp < cfg.min_judges_per_persona",
    ),
    "empty framings accepted": (SPEC, "        or not framings\n", ""),
    "facts_file not relative to the spec": (
        SPEC,
        "            path = base / path  # relative to the spec file's folder",
        "            pass",
    ),
    "extension rule on many lines": (
        SPEC,
        "return len(stripped.splitlines()) == 1 and bool(re.search(ext, stripped, re.IGNORECASE))",
        "return bool(re.search(ext, stripped, re.IGNORECASE))",
    ),
    "extension rule case-sensitive": (
        SPEC,
        "return len(stripped.splitlines()) == 1 and bool(re.search(ext, stripped, re.IGNORECASE))",
        "return len(stripped.splitlines()) == 1 and bool(re.search(ext, stripped))",
    ),
    "home or relative prefix accepted": (
        SPEC,
        "if stripped.startswith(HOME_OR_RELATIVE):",
        "if False:",
    ),
    "leading-slash facts accepted": (
        SPEC,
        'if stripped.startswith("/") and "/" in stripped[1:]:',
        "if False:",
    ),
    "leading slash alone taken as a path": (
        SPEC,
        'if stripped.startswith("/") and "/" in stripped[1:]:',
        'if stripped.startswith("/"):',
    ),
    "slash path facts accepted": (
        SPEC,
        'if len(stripped.split()) == 1 and "/" in stripped:',
        "if False:",
    ),
    "an injection pattern dropped": (DEFAULTS, "    '\\bsystem prompt\\b',\n", ""),
    "agent id and transcript mismatch accepted": (
        f"{PKG}/cli.py",
        "if found and found != a.agent_id:",
        "if False:",
    ),
    "previous round not aggregated": (
        BRIEF,
        'if rnd > 1 and rnd - 1 not in {r["round"] for r in loop}:',
        "if False:",
    ),
    "control equal to candidate": (
        BRIEF,
        "if fixed_normalise(cand) == fixed_normalise(ctrl):",
        "if False:",
    ),
    "init twice": (SPEC, 'if (run / "rubric.lock").exists():', "if False:"),
    "measure twice": (REC, 'if any(r["id"] == mid for r in rows):', "if False:"),
    "baseline dropped": (BRIEF, 'roles.append(("baseline", read_text(first)))', "pass"),
    "open table type unchecked": (
        CFG,
        "if path in OPEN_TABLES and sample is not None and _type_ok(sample, value):",
        "if path in OPEN_TABLES:",
    ),
    "invalid regex accepted": (
        CFG,
        '            errors.append(f"injection.patterns: {p!r} is not a valid expression '
        '({exc})")\n            continue',
        "            continue",
    ),
    # Redundant guards: kept as defence in depth, expected to survive (see EXPECTED_SURVIVORS).
    "max_rounds exceeded": (BRIEF, 'if rnd > spec["max_rounds"]:', "if False:"),
    "sealed round directory exists": (BRIEF, "if round_dir(run, rnd, True).exists():", "if False:"),
    # Bounds on the configuration.
    "min valid judges floor off": (
        CFG,
        'if limits["min_valid_judges"] < MIN_VALID_JUDGES_FLOOR:',
        "if False:",
    ),
    "replacement ceiling off": (
        CFG,
        'if not 0 <= limits["max_replacements"] <= MAX_REPLACEMENTS_CEILING:',
        "if False:",
    ),
    "bad share ceiling off": (CFG, "elif not 0 <= share <= MAX_BAD_SHARE_CEILING:", "elif False:"),
    "severity ceilings off": (CFG, "if caps[name] > ceiling:", "if False:"),
    "severity order off": (CFG, "if values != sorted(values):", "if False:"),
    "run bounds off": (CFG, "if not low <= value <= high:", "if False:"),
    "quote floor off": (CFG, 'if quotes["min_chars"] < MIN_QUOTE_CHARS_FLOOR:', "if False:"),
    "a default injection pattern dropped": (
        CFG,
        "    if missing:\n        errors.append(",
        "    if False:\n        errors.append(",
    ),
    "punctuation key length unchecked": (
        CFG,
        "if len(key) != 1 or _letter_or_digit(key) or key.isspace():",
        "if _letter_or_digit(key) or key.isspace():",
    ),
    "strip_chars may grow": (
        CFG,
        "    if added:\n        errors.append(",
        "    if False:\n        errors.append(",
    ),
    "strip_chars may shrink": (
        CFG,
        "    if dropped:\n        errors.append(",
        "    if False:\n        errors.append(",
    ),
    "a stripped character mapped": (CFG, "        if key in stripped:\n", "        if False:\n"),
    "a stripped character in a mapped value": (
        CFG,
        "        if stripped & set(value):\n",
        "        if False:\n",
    ),
    "samples not normalised": (
        CFG,
        "hit = re.search(p, fixed_normalise(sample))",
        "hit = re.search(p, sample)",
    ),
    "whitespace punctuation key accepted": (
        CFG,
        "if len(key) != 1 or _letter_or_digit(key) or key.isspace():",
        'if len(key) != 1 or _letter_or_digit(key) or key == " ":',
    ),
    "a pattern that matches nothing accepted": (CFG, "        if not hit:", "        if False:"),
    "unpaired samples accepted": (
        CFG,
        "if len(samples) != len(patterns):",
        "if False:",
    ),
    "letters stripped before quotes": (
        CFG,
        'if any(c.isalnum() or c.isspace() for c in text["strip_chars"]):',
        "if False:",
    ),
    "letters mapped before quotes": (CFG, "if _letter_or_digit(value):", "if False:"),
    "empty punctuation value allowed": (
        CFG,
        "if len(value) != 1 or value.isspace():",
        "if len(value) > 1 or value.isspace():",
    ),
    "multi-character punctuation value allowed": (
        CFG,
        "if len(value) != 1 or value.isspace():",
        "if not value or value.isspace():",
    ),
    "whitespace punctuation value allowed": (
        CFG,
        "if len(value) != 1 or value.isspace():",
        "if len(value) != 1:",
    ),
    "ellipsis allowed for another key": (
        CFG,
        'if not (key == ELLIPSIS and value == "..."):',
        'if value != "...":',
    ),
    # The run's [text] settings reaching a check other than quote matching. The replacement
    # imports the configured normalisation itself: the module no longer does.
    "control identity uses the configured normalisation": (
        BRIEF,
        "if fixed_normalise(cand) == fixed_normalise(ctrl):",
        f"if {CFG_NORM}(cand, cfg) == {CFG_NORM}(ctrl, cfg):",
    ),
    "whole-brief check uses the configured normalisation": (
        REC,
        "        prompt = fixed_normalise(transcript_prompt(transcript, cfg))\n",
        f"        prompt = {CFG_NORM}(transcript_prompt(transcript, cfg), cfg)\n"
        f'        brief = {CFG_NORM}(read_text(rdir / f"brief_j{{judge}}.md"), cfg)\n',
    ),
    "void brief check uses the configured normalisation": (
        REC,
        "    pasted = brief in fixed_normalise(transcript_prompt(transcript, cfg))\n",
        f'    brief = {CFG_NORM}(read_text(rdir / f"brief_j{{judge}}.md"), cfg)\n'
        f"    pasted = brief in {CFG_NORM}(transcript_prompt(transcript, cfg), cfg)\n",
    ),
    "casefold skipped": (
        CFG,
        "return any(c.isalnum() for c in s + s.casefold())",
        "return any(c.isalnum() for c in s)",
    ),
    "scan uses the configured normalisation": (
        f"{PKG}/text.py",
        "    low = fixed_normalise(text)\n",
        "    low = normalise(text, cfg)\n",
    ),
    "judges per persona unbounded (config)": (
        CFG,
        'if run["judges_per_persona"] > MAX_JUDGES_PER_PERSONA:',
        "if False:",
    ),
    "long values cut in the report": (
        CFG,
        "    if len(text) <= SHOW_LIMIT:\n        return text",
        "    if True:\n        return text[:57]",
    ),
    "overrides hidden": (CFG, "if current.get(key) != defaults.get(key):", "if False:"),
}


# Mutations no test can kill, with the guard that makes them redundant. They run and are
# reported, but do not fail the check, and the documentation does not count them as tested.
EXPECTED_SURVIVORS: dict[str, str] = {
    "max_rounds exceeded": (
        "the stop rule refuses first: at max_rounds the ledger says CAP, and "
        "'the loop has stopped' refuses the next round"
    ),
    "sealed round directory exists": (
        "'this run already has a sealed panel' refuses first: a sealed round directory "
        "exists only once its briefs are written"
    ),
}


def _copy_tree(dest: Path) -> None:
    shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "tests", dest / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", dest / "pyproject.toml")


def _env(dest: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(dest / "src")}


def _run_tests(dest: Path) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", "-o", "addopts="]
    return subprocess.run(
        cmd, cwd=dest, env=_env(dest), capture_output=True, text=True, check=False
    )


def _imports_copy(dest: Path) -> bool:
    probe = "import sealed_panel, pathlib; print(pathlib.Path(sealed_panel.__file__).resolve())"
    out = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=dest,
        env=_env(dest),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return Path(out).is_relative_to(dest.resolve())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed one defect at a time; the tests must fail.")
    ap.add_argument("--only", help="run a single mutation by name")
    a = ap.parse_args(argv)
    selected = {k: v for k, v in MUTATIONS.items() if a.only in (None, k)}
    if not selected:
        print(f"no mutation named {a.only!r}")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "baseline"
        _copy_tree(base)
        if not _imports_copy(base):
            print("the copy is not the package under test: check PYTHONPATH handling")
            return 2
        if _run_tests(base).returncode != 0:
            print("the unmutated suite fails: fix it before checking mutations")
            return 2
        survivors = []
        for i, (name, (rel, old, new)) in enumerate(selected.items()):
            dest = Path(tmp) / f"m{i}"
            _copy_tree(dest)
            target = dest / rel
            code = target.read_text(encoding="utf-8")
            if code.count(old) != 1:
                print(f"ANCHOR    {name}: found {code.count(old)} times in {rel}")
                survivors.append(name)
                continue
            target.write_text(code.replace(old, new), encoding="utf-8")
            killed = _run_tests(dest).returncode != 0
            print(f"{'killed' if killed else 'SURVIVED':9} {name}")
            if not killed:
                survivors.append(name)
    total = len(selected)
    print(f"killed {total - len(survivors)}/{total}; survivors: {survivors}")
    for name in survivors:
        if name in EXPECTED_SURVIVORS:
            print(f"expected survivor {name!r}: {EXPECTED_SURVIVORS[name]}")
    unexpected = [n for n in survivors if n not in EXPECTED_SURVIVORS]
    killed_expected = [n for n in EXPECTED_SURVIVORS if n in selected and n not in survivors]
    for name in killed_expected:
        print(f"expected survivor {name!r} was killed: remove it from EXPECTED_SURVIVORS")
    return 1 if unexpected or killed_expected else 0


if __name__ == "__main__":
    sys.exit(main())
