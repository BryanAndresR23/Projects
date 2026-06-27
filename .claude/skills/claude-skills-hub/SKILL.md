---
name: claude-skills-hub
description: Open-source marketplace for Claude skills with practical templates organized across Development, Design, Business, and Content categories. Multi-language documentation (EN, ZH, JA, KO, FR, ES).
source: https://github.com/CavinHuang/claude-skills-hub
---

# Claude Skills Hub

## Overview
Open-source marketplace for practical Claude skill templates and tools. Organized into categories for easy discovery and contribution.

## Skill Categories

### Development
Skills for software engineering workflows:
- Code review and refactoring
- API integration patterns
- Testing and debugging
- Documentation generation
- Architecture decision records

### Design
Skills for design workflows:
- UI/UX review and feedback
- Design system documentation
- Accessibility auditing
- Component specification writing
- User research synthesis

### Business
Skills for business operations:
- Business plan drafting
- Market research
- Financial modeling
- Process documentation
- Meeting facilitation and notes

### Content
Skills for content creation:
- Blog post and article writing
- Social media content
- Email campaigns
- Technical documentation
- Translation and localization

## Skill Template Structure

```
skill-name/
├── SKILL.md          # Skill definition and instructions
├── README.md         # Human-readable documentation
└── examples/         # Usage examples
```

## Standard SKILL.md Format

```markdown
---
name: skill-name
description: What this skill does and when to use it
version: 1.0.0
author: contributor-name
language: en
---

# Skill Name

## Purpose
Clear statement of what this skill accomplishes.

## Trigger
When should Claude activate this skill.

## Instructions
Step-by-step process Claude should follow.

## Output Format
What the deliverable looks like.

## Examples
Input → Output examples.
```

## Contributing
1. Fork the repository
2. Create a skill using the standard template
3. Add examples demonstrating the skill
4. Submit a pull request with category label

## Multi-Language Support
Documentation available in: English, Simplified Chinese (中文), Japanese (日本語), Korean (한국어), French (Français), Spanish (Español)
