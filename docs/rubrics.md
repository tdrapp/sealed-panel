# Rubric library

A rubric is the part of a run that takes the most care to write and that improves the most
with use. The library gives a starting rubric for common kinds of text, and collects what
sealed panels reveal about a rubric as candidates that a person can promote or reject. Nothing
changes a template on its own.

## Built-in templates

sealed-panel ships eight generic templates as package data in `src/sealed_panel/rubrics/`:

| id | for |
|---|---|
| `cv` | a CV or resume for one role |
| `cover-letter` | a letter sent with an application |
| `pitch` | a short pitch asking a decision maker for a next step |
| `message` | an email or chat message that asks for an action by a date |
| `essay` | a text that defends one thesis |
| `interview-answer` | a spoken answer to one interview question |
| `plan-doc` | a design doc, plan or proposal asking for a decision |
| `prompt-output` | the answer a model gave to a prompt you are tuning |

## Template format

A template is one JSON object with exactly these keys:

| Key | Meaning |
|---|---|
| `id` | Lowercase letters, digits and dashes. A user template with the same id overrides the built-in. |
| `version` | An integer from 1, bumped by every promotion. |
| `title` | A readable name. |
| `kinds` | The kinds of text it is for, such as `email` or `chat`. Used by `suggest`. |
| `keywords` | Words that suggest the template when they appear in the text. |
| `when_to_use` | One sentence. |
| `personas` | Placeholder personas (`id`, `description`) to adapt for each run. |
| `dimensions` | 3 to 6 dimensions, each with exactly `id`, `name`, `question` and `anchors` at `"1"`, `"3"` and `"5"`, written as observable evidence on the page. |
| `measured` | Suggested instrument criteria, in the spec's `measured` format. |
| `provenance` | A list of `{date, source, change}`, one entry per version. |

A template never holds a TODO marker. The format is checked by code every time a template is
loaded; an invalid user template is refused with the list of problems.

## Suggest

```
sealed-panel rubrics suggest --kind email --text draft.md --top 3
```

The ranking is deterministic. Each template scores 100 when the kind is one of its `kinds`
(case is ignored), plus one point per distinct keyword found as a whole word in the text. Rows
are sorted by score, then by id. Each row prints its score and a one-line reason naming the
kind match and the keywords found.

## Use

```
sealed-panel rubrics use message --out spec.json [--persona-file personas.json]
```

Writes a spec skeleton: the template's dimensions and suggested instruments, `template: {id,
version}`, and TODO markers for the title, the purpose, the facts and, without
`--persona-file`, the persona descriptions. `init` refuses the spec until every marker is
replaced. A `facts_file` you put in its place is read relative to the spec file's folder. The template reference is part of the rubric hash, so a run records which version of
which template it started from. A persona file is a JSON list of `{id, description}`.

A run holds a copy of the dimensions, not a link to the template. A later promotion changes
the template, never a frozen run.

## The user library

The user library is a directory: the `[library] path` setting (default `./rubrics/`, read from
the current directory), overridden by the `SEALED_PANEL_RUBRICS` environment variable, and by
`--library DIR` on the `rubrics` and `feedback` commands.

```
rubrics/
  message.json        a user template (overrides the built-in message)
  inbox/c-....json    open candidates
  history/c-....json  promoted or rejected candidates
```

**The user library is private by nature.** Candidates quote the texts that were judged, word
for word, and the user's own notes. Keep the library out of any public repository; this
repository ignores `/rubrics/`. Learned candidates never go into the package.

## Candidates

After a sealed panel aggregates, sealed-panel looks at the whole run and writes candidates to
the inbox, unless `[library] learn` is false. Each candidate is a JSON file with `id`,
`created`, `kind`, `status` (`open`), `template` (the run's template reference, if any),
`dimension`, `run` (rubric hash and template), `evidence` (round, judge numbers, verbatim
quotes, notes), `count`, and `proposed_change`.

| Kind | Written when |
|---|---|
| `new_dimension` | A judge's weakest point names a dimension the rubric lacks: one judge of the sealed panel, or two or more judges across the loop. |
| `sharpen_anchor` | Two or more judges of one round give the same dimension a blocking or material finding. |
| `non_discriminating` | Over two or more loop rounds, a dimension's median never moved while another dimension's did. |
| `user_disagreement` | The user records their own read with `sealed-panel feedback RUN --note TEXT [--dimension ID]`, after the seal. |

Candidates are de-duplicated: the same kind, template and dimension append their evidence to
the open candidate, and `count` says how many pieces of evidence it holds. A candidate that
was promoted or rejected is not reopened; new evidence starts a new candidate.

## Promotion

A proposed change is one of:

- `add_dimension` with a full `dimension` object;
- `rewrite_anchors` with the `dimension` id and the new `anchors` (merged over the old ones);
- `remove_dimension` with the `dimension` id.

Learned changes hold TODO markers where a person must write the new question or anchors. To
promote:

1. read the candidate in `inbox/`, and its evidence;
2. edit its `proposed_change` and replace every marker (you may change the action, for example
   to `remove_dimension` for a dimension that does not discriminate);
3. run `sealed-panel rubrics promote CANDIDATE_ID [--into TEMPLATE_ID]`.

Promotion refuses a change that still holds a marker, or that would break the template format.
It copies a built-in into the user library first if needed, applies the change, bumps the
version above both the user and the built-in version, appends a provenance entry naming the
candidate, and moves the candidate to `history/` as `promoted`. Built-in templates never
change.

To close a candidate without a change: `sealed-panel rubrics reject CANDIDATE_ID --reason
TEXT`. The candidate moves to `history/` with the reason.
