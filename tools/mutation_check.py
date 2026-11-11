"""Mutation check: each seeded defect must make the test suite fail.

Every mutation disables one refusal or one rule of the protocol, or loosens one bound. The
script copies the package and the tests to a temporary directory, applies one mutation, and
runs pytest against the copy. A mutation the tests do not catch is a survivor, and the
script exits 1.

One run of the unmutated suite under coverage maps each mutation to the tests that execute its
lines; a mutation runs those tests first, cheapest first, and a survivor is run again on the
whole suite before it is reported. CONTRIBUTING.md explains the method.

    python tools/mutation_check.py [--only NAME] [--jobs N] [--shard K/N] [--json OUT]
    python tools/mutation_check.py --merge OUT [OUT ...]
    python tools/mutation_check.py --anchors-only
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
PKG = "src/sealed_panel"
REC, AGG, SPEC, CFG, BRIEF = (
    f"{PKG}/{m}.py" for m in ("record", "aggregate", "spec", "config", "brief")
)
DEFAULTS = f"{PKG}/defaults.toml"
LEARN, LIB = f"{PKG}/learn.py", f"{PKG}/library.py"
RUN, MCP = f"{PKG}/runner.py", f"{PKG}/mcp_server.py"
TEXT = f"{PKG}/text.py"

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
    "smart quotes not normalised": (TEXT, """    "\\u201c": '"',\n""", ""),
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
        '    if (rdir / "result.json").is_file():\n'
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
        '    if (rdir / "result.json").is_file():\n'
        '        raise EvalError("this round is already aggregated: measure',
        '    if False:\n        raise EvalError("this round is already aggregated: measure',
    ),
    # Aggregation.
    "re-aggregate allowed": (
        AGG,
        '    if (rdir / "result.json").is_file():\n'
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
        "if fingerprint(raw) != read_text(lock).strip():",
        "if False:",
    ),
    "declared choice unchecked": (
        SPEC,
        'if not isinstance(c, dict) or not c.get("choice") or not c.get("user_said"):',
        "if False:",
    ),
    "spec injection allowed": (SPEC, "if injection_flags(exposed):", "if False:"),
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
    "an injection pattern dropped": (TEXT, '    r"\\bsystem prompt\\b",\n', ""),
    "agent id and transcript mismatch accepted": (
        REC,
        "if found and found != agent_id:",
        "if False:",
    ),
    "previous round not aggregated": (
        BRIEF,
        'if rnd > 1 and rnd - 1 not in {r["round"] for r in loop}:',
        "if False:",
    ),
    "control equal to candidate": (
        BRIEF,
        "if normalise(cand) == normalise(ctrl):",
        "if False:",
    ),
    "init twice": (SPEC, 'if (run / "rubric.lock").exists():', "if False:"),
    "measure twice": (REC, 'if any(r["id"] == mid for r in rows):', "if False:"),
    "baseline dropped": (BRIEF, 'roles.append(("baseline", read_text(first)))', "pass"),
    # Redundant guards: kept as defence in depth, expected to survive (see EXPECTED_SURVIVORS).
    "max_rounds exceeded": (BRIEF, 'if rnd > spec["max_rounds"]:', "if False:"),
    "sealed round directory exists": (BRIEF, "if round_dir(run, rnd, True).exists():", "if False:"),
    # The rubric library and learning.
    "auto-promotion": (
        LEARN,
        "paths = [library.add_candidate(lib, p) for p in proposals_after_seal(run)]",
        "paths = [library.promote(lib, library.add_candidate(lib, p).stem) for p in "
        "proposals_after_seal(run)]",
    ),
    "learning switch ignored": (
        LEARN,
        'if not run_config(run).library["learn"]:\n            return [], "learning off"',
        'if False:\n            return [], "learning off"',
    ),
    "learning can fail a seal": (
        LEARN,
        "    except Exception as exc:  # learning must never fail a seal",
        "    except ZeroDivisionError as exc:  # learning must never fail a seal",
    ),
    "spread threshold off by one": (
        LEARN,
        "if spread >= DISAGREE_SPREAD:",
        "if spread > DISAGREE_SPREAD:",
    ),
    "blocking contradiction ignored": (
        LEARN,
        'sev in ("blocking", "material") and raw > caps[sev]',
        'sev == "material" and raw > caps[sev]',
    ),
    "none contradiction ignored": (
        LEARN,
        'sev == "none" and raw <= 1',
        "False",
    ),
    "loop rounds read for disagreement": (
        LEARN,
        "*_judges_disagree(run, spec, sealed[-1], info, cfg),",
        "*[x for r in ledger['rounds'] for x in _judges_disagree(run, spec, r, info, cfg)],",
    ),
    "control gap off by one": (
        LEARN,
        "cand - ctrl > gap",
        "cand - ctrl >= gap",
    ),
    "control compared on capped scores": (
        LEARN,
        'raw = [r["items"].get(role, {}).get("raw_scores", {}).get(did) for r in results]',
        'raw = [r["items"].get(role, {}).get("scores", {}).get(did) for r in results]',
    ),
    "loop rounds read for the control": (
        LEARN,
        'results = _valid(run, sealed["round"], sealed=True)  # the sealed panel only',
        'results = _valid(run, sealed["round"], sealed=False)',
    ),
    "material five ignored": (
        LEARN,
        'sev in ("blocking", "material") and raw > caps[sev]',
        'sev == "blocking" and raw > caps[sev]',
    ),
    "control median not stored": (
        AGG,
        '        entry["control_median"] = statistics.median(control)',
        "        pass",
    ),
    "feedback before the seal": (LEARN, 'if d["decision"] != "SEALED":', "if False:"),
    "feedback quotes from another round": (
        LEARN,
        '    results = _valid(run, final["round"], True)\n    info',
        '    results = _valid(run, final["round"], False)\n    info',
    ),
    "quotes all marked verified": (
        LEARN,
        '"verified": did not in cand.get("unverified", []),',
        '"verified": True,',
    ),
    "dedup off": (LIB, "if existing is not None:", "if False:"),
    "dedup ignores the template version": (
        LIB,
        "return f\"{template['id']}@v{template.get('version')}{modified}\"",
        "return f\"{template['id']}{modified}\"",
    ),
    "dedup case-sensitive": (
        LIB,
        'return f"{kind}|{_template_tag(template, rubric_sha)}|{dimension.casefold()}"',
        'return f"{kind}|{_template_tag(template, rubric_sha)}|{dimension}"',
    ),
    "evidence added twice": (
        LIB,
        'if not any(_same(e, x) for x in existing["evidence"])',
        "if True",
    ),
    "rejected key reopened": (
        LIB,
        'if any(c.get("key") == key and c.get("status") in ("rejected", "promoted") for c in history):',
        "if False:",
    ),
    "rejection memory lost": (LIB, "    if earlier:\n", "    if False:\n"),
    "closed candidate reopened": (
        LIB,
        'while (inbox_dir(lib) / f"{cid}.json").exists() or '
        '(history_dir(lib) / f"{cid}.json").exists():',
        'while (inbox_dir(lib) / f"{cid}.json").exists():',
    ),
    "candidate id unchecked": (
        LIB,
        "    if not CANDIDATE_ID.match(cid):\n        raise",
        "    if False:\n        raise",
    ),
    "file id trusted": (
        LIB,
        'c["id"] = path.stem  # the file name is the id',
        "pass  # the file name is the id",
    ),
    "bad inbox file fails the listing": (
        LIB,
        '        except (OSError, ValueError) as exc:\n            warnings.append(f"skipped {path}: {exc}")',
        '        except ZeroDivisionError as exc:\n            warnings.append(f"skipped {path}: {exc}")',
    ),
    "bad user template fails the listing": (
        LIB,
        "            except (OSError, ValueError, EvalError) as exc:",
        "            except ZeroDivisionError as exc:",
    ),
    "promotion ignores TODO markers": (
        LIB,
        'if todo_paths(_used_fields(change), "proposed_change"):',
        "if False:",
    ),
    "unused TODO fields block a removal": (
        LIB,
        'if todo_paths(_used_fields(change), "proposed_change"):',
        'if todo_paths(change, "proposed_change"):',
    ),
    "closed candidate promoted": (
        LIB,
        'if not isinstance(c, dict) or c.get("status") != "open":',
        "if False:",
    ),
    "version not bumped": (
        LIB,
        'updated["version"] = max(versions) + 1',
        'updated["version"] = current["version"] + 1',
    ),
    "provenance replaced": (
        LIB,
        '    updated["provenance"] = [\n        *current["provenance"],',
        '    updated["provenance"] = [',
    ),
    "into ignored": (
        LIB,
        'tid = into or (c.get("template") or {}).get("id")',
        'tid = (c.get("template") or {}).get("id") or into',
    ),
    "promote writes beside the served file": (
        LIB,
        'target = served["path"] or lib / f"{tid}.json"',
        'target = lib / f"{tid}.json"',
    ),
    "invalid user file overwritten": (
        LIB,
        "        if w.startswith(f\"skipped {lib / (tid + '.json')}:\"):",
        "        if False:",
    ),
    "promotion not staged": (
        LIB,
        '        dump_json(staged_t, plan["updated"])\n        dump_json(staged_h, c)\n        os.replace(staged_t, target)',
        '        dump_json(target, plan["updated"])\n        dump_json(staged_h, c)',
    ),
    "reject without a reason": (LIB, "if not reason.strip():", "if False:"),
    "kind not normalised": (
        LIB,
        'return re.sub(r"[\\s_]+", "-", kind.strip().casefold())',
        "return kind",
    ),
    "aliases ignored": (
        LIB,
        'names = {normalise_kind(k) for k in [*t["kinds"], *t.get("aliases", [])]}',
        'names = {normalise_kind(k) for k in t["kinds"]}',
    ),
    "keywords match inside words": (
        LIB,
        'if re.search(r"\\b" + re.escape(k.casefold()) + r"\\b", low)',
        "if k.casefold() in low",
    ),
    "newer built-in not flagged": (
        LIB,
        'if base is not None and builtin[tid]["version"] > base:',
        "if False:",
    ),
    "skeleton thresholds kept": (
        LIB,
        '                row[bound] = _todo(f"your {bound} (suggested {row[bound]})")',
        "                pass",
    ),
    "skeleton facts hint dropped": (
        LIB,
        'hint = t.get("facts_hint") or "the facts the judges may trust"',
        'hint = "the facts the judges may trust"',
    ),
    "modified flag never set": (
        SPEC,
        'ref["modified"] = dimensions_sha(spec["dimensions"]) != ref["sha256"]',
        'ref["modified"] = False',
    ),
    "template not frozen by the hash": (
        SPEC,
        "if k not in OPTIONAL_HASH_KEYS or k in spec",
        "if k not in OPTIONAL_HASH_KEYS",
    ),
    "library not frozen at init": (
        SPEC,
        '    frozen["library"] = {**cfg.library, "path": str(lib)}\n',
        "",
    ),
    "keys missing from an old run reported": (
        CFG,
        "        if key not in current:\n            continue",
        "        if False:\n            continue",
    ),
    "promoted key reopened": (
        LIB,
        'c.get("status") in ("rejected", "promoted")',
        'c.get("status") == "rejected"',
    ),
    "modified ignored in dedup": (
        LIB,
        'modified = "+modified" if template.get("modified") else ""',
        'modified = ""',
    ),
    "runs not kept": (LIB, "            runs.append(this_run)", "            pass"),
    "another template overwritten": (
        LIB,
        'if served["path"] is None and target.exists():',
        "if False:",
    ),
    "no rollback of the first move": (
        LIB,
        "            if before is None:\n                target.unlink()\n            else:\n                target.write_bytes(before)\n",
        "            pass\n",
    ),
    "staged files left behind": (
        LIB,
        "            leftover.unlink(missing_ok=True)",
        "            pass",
    ),
    "end of input crashes the prompt": (
        f"{PKG}/cli.py",
        '    except EOFError:\n        answer = ""',
        '    except ZeroDivisionError:\n        answer = ""',
    ),
    "promote without a terminal": (f"{PKG}/cli.py", "if not sys.stdin.isatty():", "if False:"),
    "a wrong id typed is accepted": (f"{PKG}/cli.py", "if answer != a.candidate:", "if False:"),
    "inherited GIT_DIR obeyed": (
        CFG,
        "            env={k: v for k, v in os.environ.items() if k not in local},\n",
        "",
    ),
    "no usable git trusted": (
        CFG,
        "        return False  # no usable git: the library cannot be shown to be ignored\n",
        "        return True\n",
    ),
    "all GIT_ variables stripped": (
        CFG,
        "            env={k: v for k, v in os.environ.items() if k not in local},\n",
        '            env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},\n',
    ),
    "feedback library unresolved": (
        f"{PKG}/cli.py",
        "lib = library_dir(a.library) if a.library else None",
        "lib = a.library",
    ),
    "library inside the package": (
        CFG,
        "if real == package or package in real.parents:",
        "if False:",
    ),
    "library inside a git tree": (
        CFG,
        "if top is not None and not _git_ignored(top, real):",
        "if False:",
    ),
    "library checked as a file": (
        CFG,
        'str(path) + "/"],',
        "str(path)],",
    ),
    "relative library path accepted": (
        CFG,
        "            if not path.is_absolute():\n                raise ConfigError(",
        "            if False:\n                raise ConfigError(",
    ),
    "unknown template accepted": (
        SPEC,
        '    if served is None:\n        raise EvalError(\n            f"spec.template names no known template',
        '    if False:\n        raise EvalError(\n            f"spec.template names no known template',
    ),
    "unknown template version accepted": (SPEC, "    if match is None:\n", "    if False:\n"),
    "template hash trusted": (
        SPEC,
        'ref["sha256"] = dimensions_sha(match["dimensions"])',
        'ref.setdefault("sha256", dimensions_sha(match["dimensions"]))',
    ),
    "modified flag trusted": (
        SPEC,
        'ref["modified"] = dimensions_sha(spec["dimensions"]) != ref["sha256"]',
        'ref.setdefault("modified", dimensions_sha(spec["dimensions"]) != ref["sha256"])',
    ),
    "library compared lexically": (
        CFG,
        "real = path.expanduser().resolve(strict=False)",
        "real = path.expanduser().absolute()",
    ),
    "weak candidate triggers the control signal": (LEARN, "cand < floor or ", ""),
    "control gap hard-coded": (
        LEARN,
        'gap = float(cfg.library["control_gap"])',
        "gap = 1.0",
    ),
    "control_min_candidate unbounded": (
        CFG,
        'if "control_min_candidate" in library and not 1 <= library["control_min_candidate"] <= 5:',
        "if False:",
    ),
    "control_gap unbounded": (
        CFG,
        'if "control_gap" in library and not 0 <= library["control_gap"] <= 4:',
        "if False:",
    ),
    "run created before the library is resolved": (
        SPEC,
        "    lib = library_dir(library, cfg)  # resolved",
        "    run.mkdir(parents=True, exist_ok=True)\n    lib = library_dir(library, cfg)  # resolved",
    ),
    "closed id refused only after the prompt": (
        LIB,
        '    _history_file(lib, c["id"])  # a closed id',
        "    pass  # a closed id",
    ),
    "control threshold hard-coded": (
        LEARN,
        'floor = float(cfg.library["control_min_candidate"])',
        "floor = 4.0",
    ),
    "contradiction ignores the run caps": (
        LEARN,
        "    caps = cfg.severity_caps\n    results",
        '    caps = {"blocking": 3, "material": 4}\n    results',
    ),
    "promoted feedback called rejected": (
        LEARN,
        'if library.closed_status(target, proposal) == "promoted":',
        "if False:",
    ),
    "history file overwritten": (
        LIB,
        '    if dest.exists():\n        raise EvalError(f"{dest} already exists',
        '    if False:\n        raise EvalError(f"{dest} already exists',
    ),
    "init ignores --library": (
        SPEC,
        "    lib = library_dir(library, cfg)  # resolved",
        "    lib = library_dir(None, cfg)  # resolved",
    ),
    "init freezes the default library": (
        SPEC,
        'frozen["library"] = {**cfg.library, "path": str(lib)}',
        'frozen["library"] = {**cfg.library, "path": str(library_dir(None, cfg))}',
    ),
    "TODO markers accepted by init": (SPEC, "    if todo:\n", "    if False:\n"),
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
    # The run's [text] settings reaching a check other than quote matching. The replacement
    # imports the configured normalisation itself: the module no longer does.
    "a removed setting read as an unknown key": (
        CFG,
        "        if not path and key in REMOVED_SECTIONS:\n",
        "        if False:\n",
    ),
    "judges per persona unbounded (config)": (
        CFG,
        'if run["judges_per_persona"] > MAX_JUDGES_PER_PERSONA:',
        "if False:",
    ),
    "long values cut in the report": (
        CFG,
        "    if len(text) <= SHOW_LIMIT or key in SHOW_IN_FULL:\n        return text",
        "    if True:\n        return text[:57]",
    ),
    "overrides hidden": (CFG, "if current.get(key) != defaults.get(key):", "if False:"),
    # The judge runner.
    "runner without a command": (RUN, "if not command:", "if False:"),
    "runner after aggregation": (RUN, 'if (rdir / "result.json").is_file():', "if False:"),
    "a failed judge read as a reply": (RUN, "elif code != 0:", "elif False:"),
    "a timed-out judge read as a reply": (RUN, "if timed_out:", "if False:"),
    "an empty output read as a reply": (RUN, "elif not reply.strip():", "elif False:"),
    "parent environment passed to a judge": (
        RUN,
        "return {n: source[n] for n in names if n in source}",
        "return dict(source)",
    ),
    "judge started in the current directory": (RUN, "cwd=cwd,", "cwd=None,"),
    "seat refusals checked after the call": (
        RUN,
        "    _runnable(judge, prior, cfg)\n",
        "",
    ),
    "open seats unchecked": (RUN, "        _runnable(s, prior, cfg)\n", ""),
    "void cap skipped by the runner": (RUN, "    check_void_cap(judge, prior)\n", ""),
    "a valid seat run again": (
        RUN,
        'if s in busy or any(r["ok"] for r in prior):',
        "if s in busy:",
    ),
    "unknown seat in a selection accepted": (RUN, "        if unknown:\n", "        if False:\n"),
    "unknown seat run": (
        RUN,
        'if not any(isinstance(j, dict) and j.get("judge") == judge for j in keys["judges"]):',
        "if False:",
    ),
    "host reply in a runner run": (
        REC,
        '    if cfg.judge["command"]:\n        raise EvalError(\n',
        "    if False:\n        raise EvalError(\n",
    ),
    "host reply checked only in the private record": (
        REC,
        "    if source != RUNNER:\n        _refuse_in_a_runner_run(cfg)\n",
        "",
    ),
    "runner reply forged through the public API": (
        REC,
        '    if source == RUNNER:\n        raise EvalError("a runner reply is recorded',
        '    if False:\n        raise EvalError("a runner reply is recorded',
    ),
    "judge settings not checked against the server": (
        RUN,
        "    if frozen != dict(expected):",
        "    if False:",
    ),
    "judge command shown as a hash": (
        CFG,
        "if len(text) <= SHOW_LIMIT or key in SHOW_IN_FULL:",
        "if len(text) <= SHOW_LIMIT:",
    ),
    "runner proof unchecked": (REC, "if (runner is None) != (source != RUNNER):", "if False:"),
    "runner proof dropped": (REC, '        result["runner"] = runner\n', "        pass\n"),
    "judge timeout unbounded": (CFG, 'if not low <= judge["timeout_s"] <= high:', "if False:"),
    "judge timeout bound exclusive": (
        CFG,
        'if not low <= judge["timeout_s"] <= high:',
        'if not low < judge["timeout_s"] < high:',
    ),
    "judge command unchecked": (
        CFG,
        "if not all(isinstance(arg, str) and arg for arg in command):",
        "if False:",
    ),
    "judge environment names unchecked": (
        CFG,
        "if not all(isinstance(n, str) and ENV_NAME.fullmatch(n) for n in names):",
        "if False:",
    ),
    "runner replies not reported": (
        f"{PKG}/report.py",
        'if RUNNER in r.get("sources", []):',
        "if False:",
    ),
    "runner void label hidden": (
        f"{PKG}/report.py",
        '            if v.get("source") == RUNNER:',
        "            if False:",
    ),
    # The MCP server.
    "a record call with two sources accepted": (MCP, "if len(given) != 1:", "if False:"),
    "internal errors unmasked": (
        MCP,
        "        if message is None:\n            raise\n        raise ToolError(message)",
        "        raise ToolError(message or str(exc))",
    ),
    "relative paths resolved against the process directory": (
        MCP,
        "            where = contained(given if given.is_absolute() else base / given, base, repr(p))\n",
        "            where = contained(given, base, repr(p))\n",
    ),
    "seat marker not exclusive": (
        f"{PKG}/store.py",
        "    how = fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB\n",
        "    how = fcntl.LOCK_SH if wait else fcntl.LOCK_SH | fcntl.LOCK_NB\n",
    ),
    "stale marker reused without a void": (
        RUN,
        '    _record_reply(run, rnd, judge, agent, "", sealed, RUNNER, void=True, runner=proof)\n',
        "    pass\n",
    ),
    "running seat aggregated": (
        RUN,
        "            held.enter_context(seat_lock(rdir, seat))\n",
        "            pass\n",
    ),
    "output cap ignored": (RUN, "if size > cap:", "if False:"),
    "a group refusing the signal crashes the runner": (
        RUN,
        "            return\n        except OSError:\n            pass\n",
        "            return\n        except ProcessLookupError:\n            pass\n",
    ),
    "interrupted judge left unrecorded": (
        RUN,
        '        _record_reply(run, rnd, judge, agent_id, "", sealed, RUNNER, void=True, runner=proof)\n',
        "        pass\n",
    ),
    "refused reply leaves its process unrecorded": (
        RUN,
        "        if failure:\n            raise\n",
        "        raise\n",
    ),
    "a judge that used a tool is read": (REC, "    if tools_used:\n", "    if False:\n"),
    # Paths and files of a run.
    "paths outside the root accepted": (
        MCP,
        "            where = contained(given if given.is_absolute() else base / given, base, repr(p))\n",
        "            where = (given if given.is_absolute() else base / given).resolve()\n",
    ),
    "home paths accepted": (MCP, '        if p.startswith("~"):\n', "        if False:\n"),
    "root setting ignored": (
        MCP,
        "base = (root or (Path(configured) if configured else Path.cwd())).expanduser().resolve()",
        "base = (root or Path.cwd()).expanduser().resolve()",
    ),
    "init over a foreign directory": (
        SPEC,
        "if run.is_symlink() or (run.is_dir() and any(run.iterdir())):",
        "if False:",
    ),
    "links followed in a run": (
        f"{PKG}/store.py",
        "        if p.is_symlink():\n",
        "        if False:\n",
    ),
    "library inside a run accepted": (CFG, "    if run is not None:\n", "    if False:\n"),
    "root listed with the grading settings": (
        CFG,
        'NOT_GRADING = frozenset({"library.path", "mcp.root"})',
        'NOT_GRADING = frozenset({"library.path"})',
    ),
    "voids left out of the aggregate line": (
        f"{PKG}/cli.py",
        "{len(e['rejected_judges']) + len(e['voided'])} rejected or void",
        "{len(e['rejected_judges'])} rejected or void",
    ),
    "a tool call not read as a reply for a void": (
        REC,
        "replied = bool(tool_calls(transcript, cfg)) or bool(assistant_texts(transcript, cfg))",
        "replied = bool(assistant_texts(transcript, cfg))",
    ),
    "a text not read as a reply for a void": (
        REC,
        "replied = bool(tool_calls(transcript, cfg)) or bool(assistant_texts(transcript, cfg))",
        "replied = bool(tool_calls(transcript, cfg))",
    ),
    "a tool call without a reply leaves the seat stuck": (
        REC,
        "            if not judge_tools(transcript, frozen):\n                raise\n",
        "            raise\n",
    ),
    "no handback tool by default": (
        DEFAULTS,
        'handback_tool = "SubagentHandback"',
        'handback_tool = ""',
    ),
    # What the MCP server trusts.
    "a run with other settings served": (MCP, "        if differ:\n", "        if False:\n"),
    "configuration under the root accepted": (
        MCP,
        "if config_file is not None and config_file.is_relative_to(base):",
        "if False:",
    ),
    "a transcript under the root taken as proof": (
        MCP,
        "proof = transcript_path(transcript) if transcript is not None else None",
        "proof = path(transcript) if transcript is not None else None",
    ),
    "rubrics_use writes outside the root": (
        MCP,
        "rubrics_lib.write_skeleton(lib, id, path(out), opt(persona_file))",
        "rubrics_lib.write_skeleton(lib, id, Path(out), opt(persona_file))",
    ),
    "links followed in a transcript root": (
        MCP,
        "                return contained(Path(p), r)\n",
        "                return Path(p).resolve()\n",
    ),
    "facts_file outside the root read": (
        SPEC,
        "        if root is not None:\n            path = contained(",
        "        if False:\n            path = contained(",
    ),
    "init freezes no server configuration": (
        MCP,
        "digest = init_run(where, path(spec), server, opt(library), base)",
        "digest = init_run(where, path(spec), None, opt(library), base)",
    ),
    "init leaves facts_file unconfined": (
        MCP,
        "digest = init_run(where, path(spec), server, opt(library), base)",
        "digest = init_run(where, path(spec), server, opt(library))",
    ),
    "the install hint names the index": (
        f"{PKG}/cli.py",
        "'sealed-panel[mcp] @ git+https://github.com/tdrapp/sealed-panel@v{__version__}'",
        "'sealed-panel[mcp]'",
    ),
    # How a run touches the disk.
    "a failed write leaves its draft": (
        f"{PKG}/store.py",
        "            os.unlink(draft)\n",
        "            pass\n",
    ),
    "role text written through a link": (
        BRIEF,
        'write_file(rdir / f"{role}.md", text)',
        '(rdir / f"{role}.md").write_text(text)',
    ),
    "keys written through a link": (
        f"{PKG}/store.py",
        '    write_file(path, json.dumps(data, ensure_ascii=False, indent=2) + "\\n")\n',
        "    path.parent.mkdir(parents=True, exist_ok=True)\n"
        '    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\\n")\n',
    ),
    "findings written through a link": (
        f"{PKG}/report.py",
        "    write_file(path, feedback_for_author(entry))\n",
        "    path.parent.mkdir(exist_ok=True)\n    path.write_text(feedback_for_author(entry))\n",
    ),
    "a linked template served": (
        LIB,
        '            if path.is_symlink():\n                warnings.append(f"skipped {path}: a symbolic link")\n                continue\n            try:\n                t = _checked(',
        "            try:\n                t = _checked(",
    ),
    "a linked candidate read": (
        LIB,
        '        if path.is_symlink():\n            warnings.append(f"skipped {path}: a symbolic link")\n            continue\n        try:\n            c = json.loads(',
        "        try:\n            c = json.loads(",
    ),
    "a linked candidate opened": (LIB, "    refuse_links(path)\n", ""),
    "stderr mixed into the reply": (
        RUN,
        "args=(proc.stderr.fileno(), caps[1], err.write, over_err)",
        "args=(proc.stderr.fileno(), caps[1], chunks.append, over_err)",
    ),
    "stderr cap ignored": (
        RUN,
        "args=(proc.stderr.fileno(), caps[1], err.write, over_err)",
        "args=(proc.stderr.fileno(), 1 << 40, err.write, over_err)",
    ),
    "stale files ignored by a judge call": (
        RUN,
        "            with seat_lock(rdir, s):\n                _settle(run, rnd, s, sealed, rdir)\n",
        "            with seat_lock(rdir, s):\n                pass\n",
    ),
    "stale files ignored by aggregate": (
        RUN,
        "                _settle(run, rnd, seat, sealed, rdir)\n",
        "                pass\n",
    ),
    "a huge integer breaks the reader": (
        REC,
        "except (ValueError, RecursionError, EvalError) as exc:",
        "except (json.JSONDecodeError, RecursionError, EvalError) as exc:",
    ),
    "a deep reply breaks the reader": (
        REC,
        "except (ValueError, RecursionError, EvalError) as exc:",
        "except (ValueError, EvalError) as exc:",
    ),
    "a reply too deep to store is lost": (
        REC,
        "    except RecursionError:  # parsed, but too deep to write back: never a reason to lose it\n",
        "    except ZeroDivisionError:\n",
    ),
    "a server tool call not seen": (
        REC,
        """str(b.get(t["block_type_key"])).endswith(""",
        """str(b.get(t["block_type_key"])).__eq__(""",
    ),
    "ctrl-c prints a traceback": (
        f"{PKG}/cli.py",
        "    except KeyboardInterrupt:\n",
        "    except ZeroDivisionError:\n",
    ),
    "a taken seat stops the command line": (
        f"{PKG}/cli.py",
        "            except SeatTaken as exc:\n",
        "            except ZeroDivisionError as exc:\n",
    ),
    "the handback tool counted as a tool use": (
        REC,
        "if (kind, name) != exempt})",
        "if True})",
    ),
    "the handback exempt by name only": (
        REC,
        "if (kind, name) != exempt})",
        "if name != exempt[1]})",
    ),
    "a linked inbox followed": (LIB, '    refuse_links(lib, lib / "inbox")\n', ""),
    "a linked history followed": (LIB, '    refuse_links(lib, lib / "history")\n', ""),
    "a linked library folder written": (
        LIB,
        '    refuse_links(lib, lib / "inbox")\n',
        '    refuse_links(lib / "inbox")\n',
    ),
    # Round 3: shadowed settings, planted markers, moved libraries.
    "unknown keys in a frozen configuration accepted": (
        CFG,
        "        if key not in shape:\n",
        "        if False:\n",
    ),
    "a key an older run lacks read as a difference": (
        CFG,
        "here = default.get(key, _ABSENT) if isinstance(default, Mapping) else _ABSENT",
        "here = _ABSENT",
    ),
    "a frozen configuration not checked for its shape": (
        SPEC,
        "    if data is None or shape:\n",
        "    if data is None:\n",
    ),
    "stale files settled in host mode": (
        RUN,
        '            if cfg.judge["command"]:\n                _settle(',
        "            if True:\n                _settle(",
    ),
    "a marker's agent id taken": (
        RUN,
        '    agent = f"{RUNNER}-{secrets.token_hex(AGENT_BYTES)}"\n    failure = "the runner',
        '    agent = str(held.get("agent_id") or "runner-x")\n    failure = "the runner',
    ),
    "a marker's whole content kept": (
        RUN,
        "kept = {k: held[k] for k in STARTED_KEYS if k in held}",
        "kept = dict(held)",
    ),
    "a stale void on a filled seat": (
        RUN,
        '    if any(r["ok"] for r in results_by_judge(rdir).get(judge, [])):\n        return\n',
        "",
    ),
    "a library replaced by a link accepted": (
        SPEC,
        "    if path.resolve() != path:\n",
        "    if False:\n",
    ),
    "an empty transcript root argument fails": (
        f"{PKG}/cli.py",
        '        nargs="?",\n',
        "",
    ),
    "a seat at its cap fails every judge call": (
        RUN,
        "    except EvalError:\n        # At the void cap, or a round closed",
        "    except ZeroDivisionError:\n        # At the void cap, or a round closed",
    ),
    "status claims a run is served": (
        MCP,
        "served=not differ,",
        "served=True,",
    ),
    "transcript root overlap accepted": (
        MCP,
        "if real.is_relative_to(base) or base.is_relative_to(real):",
        "if False:",
    ),
    "transcript root inside the root accepted": (
        MCP,
        "if real.is_relative_to(base) or base.is_relative_to(real):",
        "if base.is_relative_to(real):",
    ),
    "root inside a transcript root accepted": (
        MCP,
        "if real.is_relative_to(base) or base.is_relative_to(real):",
        "if real.is_relative_to(base):",
    ),
    "an unset transcript root read as a folder": (
        MCP,
        '        if v and not v.startswith("$"):\n',
        "        if True:\n",
    ),
    "the home left unexpanded": (
        MCP,
        '        if v == "~" or v.startswith("~/"):\n',
        "        if False:\n",
    ),
    "$HOME left unexpanded": (
        MCP,
        '            if v == prefix or v.startswith(prefix + "/"):\n',
        "            if False:\n",
    ),
    "an overlap reason kept silent": (
        MCP,
        '                    "; ".join(turned_off)\n                    if turned_off\n',
        '                    ""\n                    if turned_off\n',
    ),
    # A transcript cut off on its last line.
    "a cut-off last line read as the earlier reply": (
        REC,
        "if lines and lines[-1][1] is None:",
        "if False:",
    ),
    "a line separator inside a string splits the line": (
        REC,
        'read_text(transcript).split("\\n")',
        "read_text(transcript).splitlines()",
    ),
    # A candidate that addresses a judge: blocked unless the spec acknowledges it.
    "a flagged candidate converges": (
        f"{PKG}/decide.py",
        "        return _baseline_gate(spec, loop, _gate(spec, last, converged))",
        "        return _baseline_gate(spec, loop, converged)",
    ),
    "a flagged sealed grade stands": (
        f"{PKG}/decide.py",
        "        return _gate(spec, sealed[-1], done)",
        "        return done",
    ),
    "the acknowledgement ignored": (
        f"{PKG}/decide.py",
        "if not flags or spec.get(INJECTION_ACK):",
        "if not flags:",
    ),
    "the acknowledgement left out of the rubric hash": (
        SPEC,
        '    "template",\n    INJECTION_ACK,\n)',
        '    "template",\n)',
    ),
    "an empty acknowledgement accepted": (
        SPEC,
        "if INJECTION_ACK in spec and not spec[INJECTION_ACK].strip():",
        "if False:",
    ),
    "a blocked report title says nothing": (
        f"{PKG}/report.py",
        '        title += f" ({BLOCKED})"',
        "        pass",
    ),
    # The judge model declared next to the grade.
    "an unbounded judge model id accepted": (
        CFG,
        "if not model_id.isprintable() or len(model_id) > MODEL_ID_MAX_CHARS:",
        "if False:",
    ),
    "the declared judge model hidden": (
        f"{PKG}/report.py",
        'return f"Judge model: `{model_id}`." if model_id else "Judge model: not declared."',
        'return "Judge model: not declared."',
    ),
    # The values the judge command ran under.
    "a changed judge environment not flagged": (
        AGG,
        "return sorted(n for n in names if len({hashes.get(n) for hashes in seen}) > 1)",
        "return []",
    ),
    "the judge environment left out of the proof": (
        RUN,
        '"env_sha256": env_hashes(cfg, env, run_salt(run)),',
        '"env_sha256": {},',
    ),
    # Every item of a brief is scanned for text addressed to a judge, and any flag blocks.
    "only the candidate is scanned": (
        BRIEF,
        '"injection_flags_by_role": flags_by_role(roles),',
        '"injection_flags_by_role": flags_by_role(roles[:1]),',
    ),
    "the gate reads the candidate's flags only": (
        f"{PKG}/decide.py",
        "    by_role = flagged_roles(entry)\n",
        '    by_role = {k: v for k, v in flagged_roles(entry).items() if k == "candidate"}\n',
    ),
    # Only a person acknowledges a flag, and the grade line says it was.
    "the MCP init freezes an acknowledgement": (
        SPEC,
        "if spec.get(INJECTION_ACK) and not acknowledge:",
        "if False:",
    ),
    "the acknowledgement left off the grade line": (
        f"{PKG}/report.py",
        "out += _sealed_headline(spec, d, model_line(cfg) + acknowledgement_line(spec))",
        "out += _sealed_headline(spec, d, model_line(cfg))",
    ),
    # The judge environment: salted per run, and compared between the loop and the seal.
    "the environment hash unsalted": (
        RUN,
        'hashlib.sha256(salt + env[n].encode("utf-8")).hexdigest()',
        'hashlib.sha256(env[n].encode("utf-8")).hexdigest()',
    ),
    "the sealed panel's environment not compared with the loop's": (
        f"{PKG}/report.py",
        "return sorted(n for n in set(sealed) | set(loop) if sealed.get(n) != loop.get(n))",
        "return []",
    ),
    # A sealed panel is never spent on a grade that could not stand.
    "a flagged round 1 still offers the seal": (
        f"{PKG}/decide.py",
        'if not flags or spec.get(INJECTION_ACK) or d["decision"] not in STOP_DECISIONS:',
        "if True:",
    ),
    "a flagged sealed brief written": (
        BRIEF,
        "if found and not spec.get(INJECTION_ACK):",
        "if False:",
    ),
    # 0.4.0: the seat lock (flock) and the one containment guard.
    "host record without the seat lock": (
        REC,
        "    with seat_lock(rdir, judge):\n        return _record_reply(",
        "    with contextlib.nullcontext():\n        return _record_reply(",
    ),
    "runner judge without the seat lock": (
        RUN,
        "    with seat_lock(rdir, judge):\n        return _judge_held(",
        "    with contextlib.nullcontext():\n        return _judge_held(",
    ),
    "a held seat listed as open": (
        RUN,
        "            with seat_lock(rdir, s):\n",
        "            with contextlib.nullcontext():\n",
    ),
    "seat lock released before the record": (
        f"{PKG}/store.py",
        "            fcntl.flock(fd, how)\n",
        "            fcntl.flock(fd, how)\n            fcntl.flock(fd, fcntl.LOCK_UN)\n",
    ),
    "a held seat lock read as unsupported": (
        f"{PKG}/store.py",
        "        except BlockingIOError as exc:\n",
        "        except ZeroDivisionError as exc:\n",
    ),
    "a filesystem without flock records unlocked": (
        f"{PKG}/store.py",
        "        except OSError as exc:\n            raise EvalError(\n",
        "        except OSError as exc:\n            yield\n            return\n            raise EvalError(\n",
    ),
    "a round aggregated meanwhile judged": (
        RUN,
        "    _refuse_closed(rdir)  # aggregated since the first check\n",
        "",
    ),
    "a path leaving its root accepted": (
        f"{PKG}/store.py",
        "    if not real.is_relative_to(root.resolve()):\n",
        "    if False:\n",
    ),
    "a run file written readable by others": (
        f"{PKG}/store.py",
        "        os.replace(draft, path)\n",
        "        os.chmod(draft, 0o644)\n        os.replace(draft, path)\n",
    ),
    # 0.4.0 review: nested runs, the run lock, leftover records.
    "a run created inside a run": (
        SPEC,
        '        if (outer / "rubric.lock").exists():\n',
        "        if False:\n",
    ),
    "a lock file that cannot be opened crashes": (
        f"{PKG}/store.py",
        '    except OSError as exc:\n        raise EvalError(f"cannot open the lock file',
        '    except ZeroDivisionError as exc:\n        raise EvalError(f"cannot open the lock file',
    ),
    "run-wide checks without the run lock": (
        REC,
        "@under_run_lock\ndef _record_reply(",
        "def _record_reply(",
    ),
    "a round briefed twice at once": (
        BRIEF,
        "@under_run_lock\ndef write_briefs(",
        "def write_briefs(",
    ),
    "a judge's stderr log readable by others": (
        RUN,
        '0o600), "wb") as err,',
        '0o644), "wb") as err,',
    ),
    "a started record of a stored attempt voided again": (
        RUN,
        '        if held.get("agent_id") not in stored:\n',
        "        if True:\n",
    ),
    "an unreadable started record blocks the seat": (
        RUN,
        "    except EvalError:\n        return {}\n",
        "    except ZeroDivisionError:\n        return {}\n",
    ),
    "concurrent readings lose one": (
        REC,
        "@under_run_lock\ndef record_measurement(",
        "def record_measurement(",
    ),
    "concurrent inits not serialised": (
        MCP,
        '            with locked(init_lock, "another init is running", wait=True):\n',
        "            with contextlib.nullcontext():\n",
    ),
    "init onto the server's lock file accepted": (
        MCP,
        "        if where.is_relative_to(init_lock):\n",
        "        if False:\n",
    ),
    "the lock file not reserved at startup": (
        MCP,
        "        os.close(os.open(lock, os.O_RDWR | os.O_CREAT, 0o600))\n",
        "        pass\n",
    ),
    "a run directory readable by others": (
        f"{PKG}/store.py",
        "        folder.mkdir(mode=0o700, exist_ok=True)\n",
        "        folder.mkdir(mode=0o777, exist_ok=True)\n",
    ),
    "a seat filled meanwhile stops the round": (
        RUN,
        '    if any(r["ok"] for r in prior):\n        raise SeatTaken(',
        "    if False:\n        raise SeatTaken(",
    ),
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

# A pytest plugin loaded into every run: it records each test's duration by full node id and
# the tests that failed, so the map can order tests and a kill can name its failing test.
PROBE = """\
import json
import os

_durations = {}
_failed = []


def pytest_runtest_logreport(report):
    _durations[report.nodeid] = _durations.get(report.nodeid, 0.0) + report.duration
    if report.failed and report.nodeid not in _failed:
        _failed.append(report.nodeid)


def pytest_sessionfinish(session, exitstatus):
    with open(os.environ["MUTATION_CHECK_PROBE"], "w", encoding="utf-8") as fh:
        json.dump({"durations": _durations, "failed": _failed}, fh)
"""
PROBE_MODULE = "_mutation_probe"

# The pytest exit code is the verdict: 0 the tests passed (the mutation survived), 1 a test
# failed (killed). 2, interrupted, is a kill flagged INTERRUPT: a mutation that lets ctrl-c
# reach pytest, or breaks an import during collection, ends the run that way. Codes 3 (an
# internal error), 4 (a usage error, such as a node id the copy lacks) and 5 (no test
# collected), and any other, mean the run itself went wrong: an error that fails the check.
OUTCOMES = {0: "survived", 1: "killed", 2: "interrupt"}
DETECTED = ("killed", "interrupt", "timeout")
TIMEOUT_FLOOR_S, TIMEOUT_FACTOR, TIMEOUT_MARGIN_S = 60.0, 5.0, 30.0


class Run(NamedTuple):
    """One pytest run: its exit code (None when it timed out), its wall time, the tests that
    failed and the duration of each test, as the probe recorded them."""

    code: int | None
    seconds: float
    failed: Sequence[str] = ()
    durations: Mapping[str, float] = {}
    output: str = ""


class Selection(NamedTuple):
    """The tests one mutation runs, cheapest first, or None for the whole suite, and why."""

    tests: list[str] | None
    reason: str = ""


def outcome(code: int | None) -> str:
    return "timeout" if code is None else OUTCOMES.get(code, "error")


def split_context(context: str) -> str:
    """The test node id of a coverage context 'nodeid|phase'. A parametrized id can hold a
    '|', the phase never does, so the split is on the last one."""
    return context.rsplit("|", 1)[0]


def in_shard(name: str, k: int, n: int) -> bool:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest(), 16) % n == k


def timeout_for(baseline_s: float) -> float:
    return max(TIMEOUT_FLOOR_S, TIMEOUT_FACTOR * baseline_s + TIMEOUT_MARGIN_S)


def _copy_tree(dest: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__")
    for name in ("src", "tests", "examples"):
        shutil.copytree(ROOT / name, dest / name, ignore=ignore)
    shutil.copy(ROOT / "pyproject.toml", dest / "pyproject.toml")


def _env(dest: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(dest / "src")}


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


def _pytest(
    work: Path,
    tests: list[str] | None,
    timeout: float | None,
    extra: Sequence[str] = (),
    extra_env: Mapping[str, str] | None = None,
) -> Run:
    """Run pytest -x in work/tree on the given node ids in that order, or on the whole suite.
    The ids go through an argfile, so no id is split by a shell or a length limit. The run has
    its own TMPDIR and its own process group, and a timeout kills the whole group."""
    tree, probe, report = work / "tree", work / "probe", work / "probe.json"
    (work / "tmp").mkdir(exist_ok=True)
    probe.mkdir(exist_ok=True)
    (probe / f"{PROBE_MODULE}.py").write_text(PROBE, encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "-x",
        "-q",
        "-p",
        "no:cacheprovider",
        "-o",
        "addopts=",
    ]
    cmd += ["-p", PROBE_MODULE, *extra]
    if tests is not None:
        argfile = work / "tests.txt"
        argfile.write_text("".join(f"{t}\n" for t in tests), encoding="utf-8")
        cmd.append(f"@{argfile}")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tree / "src"), str(probe)]),
        "TMPDIR": str(work / "tmp"),
        "MUTATION_CHECK_PROBE": str(report),
        **(extra_env or {}),
    }
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=tree,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    code: int | None
    try:
        out, _ = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        out, _ = proc.communicate()
        code = None
    seconds = time.monotonic() - start
    data = json.loads(report.read_text(encoding="utf-8")) if report.exists() else {}
    return Run(code, seconds, data.get("failed", []), data.get("durations", {}), out)


def _anchor_lines(src: str, old: str) -> set[int]:
    """The lines of the anchor, plus the first line of each statement it overlaps: coverage
    records a statement on its first line. A compound statement's first line counts only when
    the anchor reaches into its header, not when the anchor is only in its body."""
    lo = src.count("\n", 0, src.index(old)) + 1
    hi = lo + old.rstrip("\n").count("\n")
    lines = set(range(lo, hi + 1))
    compound = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.With,
        ast.AsyncWith,
        ast.Try,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.stmt) or node.lineno > hi or (node.end_lineno or 0) < lo:
            continue
        body = getattr(node, "body", None)
        if not isinstance(node, compound) or not body or lo < body[0].lineno:
            lines.add(node.lineno)
    return lines


def build_map(work: Path, names: Iterable[str]) -> tuple[dict[str, Selection], Run]:
    """Run the unmutated suite once under coverage, one context per test, then select for
    each mutation the tests that execute its anchor lines, cheapest first. A mutation outside
    Python, or on lines no test executes (run at import only), gets the whole suite."""
    from coverage import CoverageData

    data_file = work / "coverage.data"
    cov = ["--cov=sealed_panel", "--cov-context=test", "--cov-report="]
    run = _pytest(work, None, None, cov, {"COVERAGE_FILE": str(data_file)})
    if run.code != 0:
        return {}, run
    data = CoverageData(basename=str(data_file))
    data.read()
    measured = {Path(f).resolve(): f for f in data.measured_files()}
    by_line: dict[Path, dict[int, list[str]]] = {}
    selection: dict[str, Selection] = {}
    for name in names:
        rel, old, _new = MUTATIONS[name]
        path = (work / "tree" / rel).resolve()
        src = path.read_text(encoding="utf-8")
        if not rel.endswith(".py"):
            selection[name] = Selection(None, "not Python")
            continue
        if src.count(old) != 1 or path not in measured:
            selection[name] = Selection(None, "not measured")
            continue
        if path not in by_line:
            by_line[path] = data.contexts_by_lineno(measured[path])
        contexts = {c for line in _anchor_lines(src, old) for c in by_line[path].get(line, [])}
        tests = {split_context(c) for c in contexts if c}
        if not tests:
            selection[name] = Selection(None, "no test executes it")
            continue
        selection[name] = Selection(sorted(tests, key=lambda t: (run.durations.get(t, 0.0), t)))
    return selection, run


class Checker:
    """Runs mutations one by one, each in a fresh copy of the tree that is deleted after."""

    def __init__(
        self,
        tmp: Path,
        selection: Mapping[str, Selection],
        durations: Mapping[str, float],
    ):
        self.tmp, self.selection, self.durations = tmp, selection, durations
        self.full_timeout = timeout_for(sum(durations.values()))
        self._clean: dict[tuple[str, ...] | None, int | None] = {}
        self._lock = threading.Lock()

    def _run(self, name: str | None, tests: list[str] | None, timeout: float) -> Run:
        work = Path(tempfile.mkdtemp(dir=self.tmp))
        try:
            _copy_tree(work / "tree")
            if name is not None:
                rel, old, new = MUTATIONS[name]
                target = work / "tree" / rel
                target.write_text(
                    target.read_text(encoding="utf-8").replace(old, new),
                    encoding="utf-8",
                )
            return _pytest(work, tests, timeout)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _passes_unmutated(self, tests: list[str] | None) -> bool:
        """Whether these tests (None: the whole suite), in this order, pass on unmutated code
        within their time limit. A subset can fail where the whole suite passes (a test that
        leans on an earlier one), and a slow machine can time out without any mutation: in
        both cases a kill or a timeout would be false."""
        key = None if tests is None else tuple(tests)
        with self._lock:
            if key in self._clean:
                return self._clean[key] == 0
        code = self._run(None, tests, self._timeout(tests)).code
        with self._lock:
            self._clean[key] = code
        return code == 0

    def _timeout(self, tests: list[str] | None) -> float:
        if tests is None:
            return self.full_timeout
        return timeout_for(sum(self.durations.get(t, 0.0) for t in tests))

    def check(self, name: str) -> dict[str, Any]:
        start = time.monotonic()
        rel, old, _new = MUTATIONS[name]
        found = (ROOT / rel).read_text(encoding="utf-8").count(old)
        sel = self.selection.get(name, Selection(None, "whole suite"))
        result: dict[str, Any] = {
            "name": name,
            "selected": None if sel.tests is None else len(sel.tests),
            "reason": sel.reason,
            "confirmed": False,
            "failed": None,
            "note": "",
        }
        if found != 1:
            result.update(outcome="anchor", note=f"anchor found {found} times in {rel}")
            result["seconds"] = round(time.monotonic() - start, 2)
            return result
        run = self._run(name, sel.tests, self._timeout(sel.tests))
        verdict = outcome(run.code)
        if verdict in ("killed", "interrupt") and sel.tests is not None:
            # The same tests must pass on unmutated code, up to the one that failed, or the
            # kill (an interrupt above all) may come from the selection, not the mutation.
            first = run.failed[0] if run.failed else None
            prefix = sel.tests[: sel.tests.index(first) + 1] if first in sel.tests else sel.tests
            if not self._passes_unmutated(prefix):
                verdict = "error"
                result["note"] = "the selected tests fail on unmutated code"
        if verdict == "survived" and sel.tests is not None:
            # The selection is a shortcut; a survivor is reported only once the whole suite
            # has missed it too.
            run = self._run(name, None, self.full_timeout)
            verdict = outcome(run.code)
            result["confirmed"] = True
            if verdict == "timeout":
                # The selected tests passed and the others never run the mutated line, so a
                # timeout here says the machine is slow, not that the mutation is caught.
                verdict = "error"
                result["note"] = "the whole suite timed out on a survivor of the selection"
        if verdict == "error" and not result["note"]:
            result["note"] = f"pytest exit code {run.code}: " + " | ".join(
                run.output.strip().splitlines()[-5:]
            )
        result.update(outcome=verdict, failed=run.failed[0] if run.failed else None)
        result["seconds"] = round(time.monotonic() - start, 2)
        return result

    def retry_timeout(self, name: str) -> dict[str, Any]:
        """Run a mutation that timed out once more, alone. A second timeout counts as detected
        only if the same tests finish within the same limit on unmutated code."""
        r = self.check(name)
        notes = [r["note"]] if r["note"] else []
        notes.append("timed out once, run again alone")
        if r["outcome"] == "timeout" and not self._passes_unmutated(
            self.selection.get(name, Selection(None)).tests
        ):
            r["outcome"] = "error"
            notes.append("the same tests do not pass within the limit on unmutated code")
        r["note"] = "; ".join(notes)
        return r


def _line(r: Mapping[str, Any]) -> str:
    sel = "all" if r["selected"] is None else str(r["selected"])
    label = r["outcome"] if r["outcome"] in DETECTED else r["outcome"].upper()
    note = f"  ({r['note']})" if r["note"] else ""
    return f"{label:9} {r['seconds']:7.1f}s {sel:>4} {r['name']}{note}"


def judge(outcomes: Mapping[str, str]) -> int:
    """Apply the rule to the outcome of each mutation run: every one is detected, except the
    expected survivors, which must survive; any error or broken anchor fails the check."""
    survivors = [n for n, o in outcomes.items() if o == "survived"]
    broken = [n for n, o in outcomes.items() if o not in (*DETECTED, "survived")]
    detected = len(outcomes) - len(survivors) - len(broken)
    print(f"detected {detected}/{len(outcomes)}; survivors: {survivors}; errors: {broken}")
    interrupts = [n for n, o in outcomes.items() if o == "interrupt"]
    if interrupts:
        print(f"killed by an interrupt (pytest exit code 2): {interrupts}")
    for name in survivors:
        if name in EXPECTED_SURVIVORS:
            print(f"expected survivor {name!r}: {EXPECTED_SURVIVORS[name]}")
    unexpected = [n for n in survivors if n not in EXPECTED_SURVIVORS]
    killed_expected = [n for n in EXPECTED_SURVIVORS if n in outcomes and n not in survivors]
    for name in killed_expected:
        print(f"expected survivor {name!r} was not a survivor: check it, then update the list")
    return 1 if unexpected or killed_expected or broken else 0


def merge(paths: Sequence[Path]) -> int:
    """Merge the shard reports: together they must hold every mutation exactly once."""
    outcomes: dict[str, str] = {}
    twice: list[str] = []
    for path in paths:
        for r in json.loads(path.read_text(encoding="utf-8"))["results"]:
            if r["name"] in outcomes:
                twice.append(r["name"])
            outcomes[r["name"]] = r["outcome"]
    missing = [n for n in MUTATIONS if n not in outcomes]
    unknown = [n for n in outcomes if n not in MUTATIONS]
    for label, names in (
        ("not run", missing),
        ("run twice", twice),
        ("unknown", unknown),
    ):
        if names:
            print(f"{label}: {names}")
    return judge(outcomes) or (1 if missing or twice or unknown else 0)


def anchor_problems(names: Iterable[str]) -> list[str]:
    """Each mutation whose anchor is not found exactly once in its file, with the count. No
    test runs: a code change that moves an anchor shows here in a second, not after an hour."""
    problems = []
    for name in names:
        rel, old, _new = MUTATIONS[name]
        found = (ROOT / rel).read_text(encoding="utf-8").count(old)
        if found != 1:
            problems.append(f"{name}: anchor found {found} times in {rel}")
    return problems


def check_anchors(names: Sequence[str]) -> int:
    """Print every anchor problem and a count; 1 when there is one."""
    problems = anchor_problems(names)
    for line in problems:
        print(line)
    print(f"{len(names) - len(problems)} of {len(names)} anchors found exactly once")
    return 1 if problems else 0


def _sharded(ap: argparse.ArgumentParser, names: list[str], shard: str | None) -> list[str]:
    """The mutations of shard k/n, or all of them without a shard."""
    if not shard:
        return names
    k, n = (int(x) for x in shard.split("/"))
    if not 0 <= k < n:
        ap.error("--shard needs k/n with 0 <= k < n")
    return [m for m in names if in_shard(m, k, n)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed one defect at a time; the tests must fail.")
    ap.add_argument("--only", help="run a single mutation by name")
    ap.add_argument("--jobs", type=int, default=1, help="mutations run at once (default 1)")
    ap.add_argument("--shard", help="k/n: run only the mutations whose hash is k modulo n")
    ap.add_argument("--json", type=Path, help="write each mutation's outcome to this file")
    ap.add_argument(
        "--full-suite", action="store_true", help="run the whole suite on each mutation"
    )
    ap.add_argument("--merge", nargs="+", type=Path, help="merge shard reports and judge them")
    ap.add_argument(
        "--anchors-only",
        action="store_true",
        help="check that every anchor is found exactly once, and run no test",
    )
    a = ap.parse_args(argv)
    if a.merge:
        return merge(a.merge)
    names = [n for n in MUTATIONS if a.only in (None, n)]
    if not names:
        print(f"no mutation named {a.only!r}")
        return 2
    if a.anchors_only:
        return check_anchors(names)
    names = _sharded(ap, names, a.shard)
    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "baseline"
        _copy_tree(base / "tree")
        if not _imports_copy(base / "tree"):
            print("the copy is not the package under test: check PYTHONPATH handling")
            return 2
        if a.full_suite:
            selection: dict[str, Selection] = {}
            baseline = _pytest(base, None, None)
        else:
            selection, baseline = build_map(base, names)
        if baseline.code != 0:
            print("the unmutated suite fails: fix it before checking mutations")
            print(baseline.output[-3000:])
            return 2
        mapped = sum(1 for s in selection.values() if s.tests is not None)
        print(
            f"{len(names)} mutations, {mapped} with selected tests; unmutated suite "
            f"{baseline.seconds:.1f}s; {a.jobs} at once",
            flush=True,
        )
        checker = Checker(Path(tmp), selection, baseline.durations)
        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=a.jobs) as pool:
            for r in pool.map(checker.check, names):
                results[r["name"]] = r
                print(_line(r), flush=True)
        for name in [n for n, r in results.items() if r["outcome"] == "timeout"]:
            # A timeout under load may be the load: run it again, alone, before accepting it.
            r = checker.retry_timeout(name)
            results[name] = r
            print(_line(r), flush=True)
    wall = time.monotonic() - started
    print(f"wall {wall:.1f}s", flush=True)
    if a.json:
        report = {
            "shard": a.shard,
            "jobs": a.jobs,
            "full_suite": a.full_suite,
            "wall_s": wall,
        }
        report["results"] = [results[n] for n in names]
        a.json.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    return judge({n: results[n]["outcome"] for n in names})


if __name__ == "__main__":
    sys.exit(main())
