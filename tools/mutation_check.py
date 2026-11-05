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
LEARN, LIB = f"{PKG}/learn.py", f"{PKG}/library.py"
RUN, MCP = f"{PKG}/runner.py", f"{PKG}/mcp_server.py"

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
    "re-roll hash with run config": (
        BRIEF,
        'if text_hash(cand) in {r.get("candidate_hash") for r in loop}:',
        f'if __import__("hashlib").sha256({CFG_NORM}(cand, cfg).encode("utf-8")).hexdigest()'
        '[:16] in {r.get("candidate_hash") for r in loop}:',
    ),
    "baseline inclusion with run config": (
        BRIEF,
        "if first.exists() and fixed_normalise(read_text(first)) != fixed_normalise(cand):",
        f"if first.exists() and {CFG_NORM}(read_text(first), cfg) != {CFG_NORM}(cand, cfg):",
    ),
    "previous inclusion with run config": (
        BRIEF,
        "if prev_path.exists() and fixed_normalise(read_text(prev_path)) != fixed_normalise(cand):",
        f"if prev_path.exists() and {CFG_NORM}(read_text(prev_path), cfg)"
        f" != {CFG_NORM}(cand, cfg):",
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
    "one-quote flag with run config": (
        REC,
        "quotes.append(fixed_normalise(quote))",
        f"quotes.append({CFG_NORM}(quote, cfg))",
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
    # The judge runner.
    "runner without a command": (RUN, "if not command:", "if False:"),
    "runner after aggregation": (RUN, 'if (rdir / "result.json").exists():', "if False:"),
    "a failed judge read as a reply": (RUN, "elif code != 0:", "elif False:"),
    "a timed-out judge read as a reply": (RUN, "if timed_out:", "if False:"),
    "an empty output read as a reply": (RUN, "elif not reply.strip():", "elif False:"),
    "parent environment passed to a judge": (
        RUN,
        "return {n: source[n] for n in names if n in source}",
        "return dict(source)",
    ),
    "judge started in the current directory": (RUN, "cwd=cwd,", "cwd=None,"),
    "stderr mixed into the reply": (RUN, "stderr=err,", "stderr=subprocess.STDOUT,"),
    "brief altered on stdin": (
        RUN,
        "proc.communicate(brief, timeout=timeout)",
        'proc.communicate(brief + b"\\n", timeout=timeout)',
    ),
    "seat refusals checked after the call": (
        RUN,
        "    _runnable(judge, results_by_judge(rdir).get(judge, []), cfg)\n",
        "",
    ),
    "open seats unchecked": (RUN, "        _runnable(s, prior, cfg)\n", ""),
    "void cap skipped by the runner": (RUN, "    check_void_cap(judge, prior)\n", ""),
    "a valid seat run again": (
        RUN,
        '        if any(r["ok"] for r in prior):\n            continue\n',
        "",
    ),
    "unknown seat in a selection accepted": (RUN, "        if unknown:\n", "        if False:\n"),
    "unknown seat run": (
        RUN,
        'if not any(isinstance(j, dict) and j.get("judge") == judge for j in keys["judges"]):',
        "if False:",
    ),
    "pasted reply on a runner seat": (
        REC,
        'if source == "output" and any(r.get("source") == RUNNER for r in prior):',
        "if False:",
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
        "return (given if given.is_absolute() else base / given).resolve()",
        "return given.resolve()",
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


def _copy_tree(dest: Path) -> None:
    shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "tests", dest / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        ROOT / "examples", dest / "examples", ignore=shutil.ignore_patterns("__pycache__")
    )
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
