# Example: a judge command for the runner

`sealed-panel judge` starts a judge command once per brief, writes the brief on its stdin and
records its stdout as the reply. These files show how to plug in your model.

| File | Role |
|---|---|
| `judge_template.py` | **Template.** Reads the brief on stdin, sends it to your model through a model command line (its arguments after `--`) or an HTTP endpoint (`--url URL`, with `JUDGE_MODEL_KEY` as a bearer token), and prints the answer. Adapt `ask_http` to your model's API. |
| `judge.toml` | The `[judge]` section to give to `init --config`: the command with the model in it, the timeout, the credential's name, and `model_id`. |
| `library_usage.py` | A one-shot sealed grade from Python, through the same functions the command line calls. |

With a model command line on your path, named in `judge.toml` after `--`:

```sh
sealed-panel init runs/demo --spec spec.json --config examples/runner/judge.toml
sealed-panel brief runs/demo --sealed --artefact draft.md --control control.md
sealed-panel judge runs/demo --sealed
sealed-panel aggregate runs/demo --sealed
sealed-panel report runs/demo
```

Edit the command path in `judge.toml` first: the runner starts the judge in an empty directory,
so a relative path finds nothing. Keep credentials out of `command`, which is stored with every
reply; list their names in `env_allow` instead. Keep the model in `command`, never in a
variable: the run freezes the command and shows it next to the grade, while of a variable it
keeps only a hash of the value, so a model changed there is flagged in the report but not named.
The template still reads `JUDGE_MODEL_COMMAND` or `JUDGE_MODEL_URL` when it gets no argument,
for configurations written before 0.3.1.

A judge model gets no tools: the template's instructions forbid them, and a model command line
should be started without any (no file access, no shell). One call per brief: a command that
asks several times and prints the best answer is a re-roll the runner cannot see.
