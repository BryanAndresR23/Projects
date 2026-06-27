---
name: skill-creator
description: Guides building new Claude Code skills — modular packages that extend Claude's capabilities with specialized knowledge, workflows, and tool integrations. Use when creating a new SKILL.md package.
source: https://github.com/ComposioHQ/awesome-claude-skills
---

# Skill Creator

## Overview
Build modular Claude skill packages that extend Claude's capabilities with specialized knowledge, workflows, and tool integrations. Skills are reusable instruction packages that teach Claude how to handle a specific class of tasks.

## Skill Structure

```
skill-name/
├── SKILL.md          # Required: metadata + instructions
├── scripts/          # Optional: executable code for deterministic tasks
├── references/       # Optional: documentation loaded on-demand
└── assets/           # Optional: output templates, images, etc.
```

## SKILL.md Template

```markdown
---
name: skill-name
description: When Claude should use this skill and what it does. Be specific.
---

# Skill Title

## Overview
What this skill does and why it exists.

## When to Use
- Specific trigger condition 1
- Specific trigger condition 2

## Process
Step-by-step instructions written in imperative voice for Claude to follow.

## Examples
Concrete examples of inputs and expected outputs.
```

## Progressive Disclosure Design
The system loads content efficiently:
- **Metadata** (name, description) — always available, used for skill matching
- **Full SKILL.md instructions** — loaded when skill is triggered
- **References** — loaded on-demand during execution

Keep SKILL.md lean. Move detailed reference material to `references/` files.

## Six-Step Creation Process

1. **Understand through examples** — Gather 3–5 concrete usage scenarios
2. **Plan reusable contents** — Identify what scripts, references, and assets are needed
3. **Initialize** — Create directory structure
4. **Edit** — Write resources and complete SKILL.md using imperative language
5. **Validate** — Test with real prompts, verify skill triggers correctly
6. **Iterate** — Refine based on real-world testing feedback

## Writing Principles

- **Imperative voice**: "Check the file exists before reading" not "The file should be checked"
- **Non-obvious knowledge only**: Don't document what Claude already knows — document domain-specific constraints, hidden invariants, and workflow quirks
- **Concrete over abstract**: "Run `npm test` then check `coverage/index.html`" not "run tests and review coverage"
- **Write for other Claude instances**: This is documentation for Claude, not for humans

## References Directory
Move large reference content out of SKILL.md:
```
references/
├── api-endpoints.md      # API documentation
├── error-codes.md        # Known error codes and fixes
└── style-guide.md        # Formatting conventions
```

Reference files are loaded only when needed, preventing context window bloat.

## Validation Checklist
- [ ] Skill triggers on correct prompts
- [ ] Description is specific enough to distinguish from other skills
- [ ] Instructions are actionable, not aspirational
- [ ] Examples cover edge cases
- [ ] References don't duplicate SKILL.md content
