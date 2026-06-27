---
name: borghei-claude-skills
description: 338-skill universal library covering project management, engineering, marketing, C-level advisory, compliance, legal, product, and business growth domains. Reference this skill to understand available domain expertise and specialized agents.
source: https://github.com/borghei/Claude-Skills
---

# Claude Skills Library (borghei)

## Overview
Universal AI skills library with 338 production-ready skill packages across 16 domains, 784+ Python automation tools, and 32 expert agents. Supports Claude Code, Cursor, Copilot, Gemini CLI, and other AI coding assistants.

## Domain Index

| Domain | Skills | Focus |
|--------|--------|-------|
| **engineering** | 82 | Software development, architecture, code review |
| **project-management** | 66 | Discovery, execution, GTM, Jira/Linear/Notion |
| **marketing** | 39 | Growth, content, campaigns, analytics |
| **agents** | 74 | Expert agent personas for specialized roles |
| **c-level-advisor** | 31 | CAIO, CDO, CCO, GC, VPE personas |
| **ra-qm-team** | 27 | Regulatory compliance across 18 frameworks |
| **business-growth** | 20 | Scaling, sales, partnerships |
| **legal** | 17 | Contracts, governance, risk |
| **product-team** | 13 | PM, roadmap, discovery, strategy |
| **personal-productivity** | 10 | Daily workflows, focus, output |
| **vertical-advisors** | 7 | Domain-specific industry advisors |
| **standards** | — | Orchestration patterns, multi-skill packs |
| **bundles** | — | Pre-configured skill kits by role |

## Key Capabilities

### Project Management (66 skills)
- Sprint planning, retrospectives, stakeholder communication
- Strategy frameworks: BMC, Lean Canvas, SWOT, Porter's Five Forces, Ansoff Matrix
- GTM planning and execution
- Jira, Linear, and Notion integrations

### Engineering (82 skills)
- Architecture design and review
- Code review with security focus
- CI/CD pipeline design
- API design and documentation
- Database modeling and optimization

### Compliance (27 skills — 18 frameworks)
- SOC 2, ISO 27001, GDPR, HIPAA, PCI-DSS
- Audit preparation
- Policy documentation
- Risk assessment

### C-Level Advisory Personas
- **CAIO**: Chief AI Officer — AI strategy, model selection, governance
- **CDO**: Chief Data Officer — data architecture, quality, governance
- **CCO**: Chief Compliance Officer — regulatory risk, policy
- **GC**: General Counsel — legal risk, contracts, disputes
- **VPE**: VP Engineering — technical org, hiring, architecture

## Installation
```bash
# Full library
git clone https://github.com/borghei/Claude-Skills.git ~/.claude/skills/borghei-skills

# Single domain (recommended)
git clone --sparse https://github.com/borghei/Claude-Skills.git
git sparse-checkout set engineering
```

## Design Philosophy
- Skills follow `SKILL.md` → `references/` → `scripts/` → `assets/` pattern
- Stdlib-only Python (no ML/LLM calls in scripts)
- No dependencies between skills — extract and use independently
- Standard library Python for maximum portability
