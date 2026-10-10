# Humanizer (vendored)

Upstream: [blader/humanizer](https://github.com/blader/humanizer) @ **3.1.0** (MIT).

Methodology comes from Wikipedia’s [Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing) — pattern-based editorial de-slopping, not “beat the detector” hacks.

## Refresh from upstream

```bash
curl -sfL https://raw.githubusercontent.com/blader/humanizer/main/SKILL.md \
  -o .cursor/skills/humanizer/SKILL.md
```

After pulling, re-apply the dbscale-specific lines in the YAML `description` block (see git diff on this file’s sibling `SKILL.md`).

## In this repo

- **Skill:** `.cursor/skills/humanizer/SKILL.md` — full rewrite workflow.
- **Rule:** `.cursor/rules/customer-copy-humanizer.mdc` — loads the skill for user-visible text.

Manual invoke in chat: ask to humanize a passage or file, or say “use humanizer on …”.
