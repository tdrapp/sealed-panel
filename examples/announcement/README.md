# Example: a build cache announcement

A synthetic run. The text is an internal email announcing a remote build cache; the reader is
an engineering manager who gets several tooling announcements a month and decides what the
team spends its next sprint on.

| File | Role |
|---|---|
| `spec.json` | The rubric: one persona, three dimensions (the ask, relevance, evidence), the facts file, and one instrument (word count, 150 or fewer). |
| `facts.md` | The facts the judges may trust, frozen by content at `init`. The path in `spec.json` is relative to the spec's folder. |
| `draft_v1.md` | The candidate. It states the gain in CI minutes and gives a date, but addresses the risk to releases only through the opt-out flag. |
| `control.md` | The deliberately bad version: same topic, length and tone, but generic, with no number, no date and no concrete ask. A judge who scores it above 2 on average is discarded. |
| `play_judges.py` | **Demo only.** Writes one reply per brief with fixed scores, so the walk-through runs without a model. Its replies say nothing about the text. |

The spec sets `max_rounds` to 1 and `judges_per_persona` to 2 so the walk-through in the main
README stays short. A real run keeps the defaults (6 rounds, 3 judges per persona): round 1,
then `status` returns CONTINUE and `feedback/after_r1.md` holds the findings, the author
revises into `draft_v2.md`, and round 2 briefs the new text with round 1's text as a blind
comparator.

The steps, from the repository root:

```sh
sealed-panel init runs/announcement --spec examples/announcement/spec.json
sealed-panel brief runs/announcement --round 1 \
  --artefact examples/announcement/draft_v1.md --control examples/announcement/control.md
python examples/announcement/play_judges.py runs/announcement --round 1 --out replies
sealed-panel record runs/announcement --round 1 --judge 1 --agent-id judge-a --output replies/r1_j1.json
sealed-panel record runs/announcement --round 1 --judge 2 --agent-id judge-b --output replies/r1_j2.json
sealed-panel measure runs/announcement --round 1 --id length \
  --value "$(wc -w < examples/announcement/draft_v1.md | tr -d ' ')"
sealed-panel aggregate runs/announcement --round 1
sealed-panel status runs/announcement
# then the sealed panel, as in the main README, and:
sealed-panel report runs/announcement
```

The texts are invented. Any resemblance to a real team or tool is not intended.
