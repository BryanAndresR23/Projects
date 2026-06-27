---
name: alirezarezvani-claude-skills
description: 345-skill production-ready library across 17 domains and 78 marketplace plugins. Covers engineering, marketing, product, research, compliance, C-level advisory, and business operations. Reference this for domain expertise spanning the full software development and business lifecycle.
source: https://github.com/alirezarezvani/claude-skills
---

# Claude Code Skills Library (alirezarezvani)

## Overview
345 production-ready Claude Code skills across 17 domains, distributed as 78 marketplace plugins. Contains 579 Python automation tools, 705 reference guides, 93 agents, and 99 slash commands.

## Domain Index

| Domain | Description |
|--------|-------------|
| **engineering/** | Core software development — architecture, code review, debugging, API design |
| **engineering-team/** | Team-focused — PR reviews, onboarding, tech debt, mentoring |
| **product-team/** | Product management — roadmap, PRD writing, user stories, prioritization |
| **marketing-skill/** | Marketing — campaigns, content, SEO, analytics |
| **marketing/** | Landing page generation |
| **research/** | Academic research — literature review, citation management |
| **research-ops/** | Enterprise research — clinical, financial, market, product research |
| **project-management/** | PM and agile — sprints, retrospectives, stakeholder comms |
| **ra-qm-team/** | Regulatory and quality management |
| **compliance-os/** | Compliance framework — SOC2, ISO, GDPR, HIPAA |
| **c-level-advisor/** | C-suite personas — CEO, CTO, CFO, CMO, CPO, CAIO advisors |
| **business-growth/** | Growth and sales — GTM, sales enablement, partner programs |
| **business-operations/** | Process and ops — capacity planning, vendor management |
| **commercial/** | Pricing, deals, RFP responses, partnerships |
| **finance/** | Financial modeling, analysis, forecasting |
| **productivity/** | Daily productivity — focus, task management, async comms |
| **markdown-html/** | Convert markdown to rich HTML artifacts |

## Key Skill Patterns

### Skill Structure
```
skill-name/
├── SKILL.md          # Master documentation
├── scripts/          # Python CLI tools (stdlib only, no ML)
├── references/       # Expert knowledge bases (705 guides)
└── assets/           # User templates
```

### Design Principles
1. **Portability** — Stdlib-only Python; no ML/LLM calls in scripts
2. **Determinism** — Explicit assumptions, verifiable success criteria
3. **Documentation-driven** — Each reference doc cites 5+ authoritative sources
4. **Human-in-the-loop** — Skills route to named humans for irreversible decisions
5. **Modularity** — No dependencies between skills; extract and use independently

## Recent Additions (v2.10.x)

### markdown-html/ Domain
Operationalizes the principle: markdown collapses past ~100 lines; HTML restores clarity and interaction. Five skills:
- `orchestrator` — Routes to appropriate rendering skill
- `design-system` — Consistent HTML component library
- `md-document` — Long-form document rendering
- `md-review` — Code/PR review formatting
- `md-slides` — Presentation slide generation

Output: Single-file, lightweight HTML artifacts (11–23 KB typical)

### research-ops/ Domain
Enterprise research with mandatory human decision gates:
- `clinical-research` — Evidence synthesis with bias assessment
- `research-finance` — Investment and market research
- `market-research` — TAM/SAM/SOM, competitive analysis
- `product-research` — User research, concept validation

## Navigation by Use Case

| Task | Domain |
|------|--------|
| Code review | engineering-team/ |
| Architecture design | engineering/ |
| Writing a PRD | product-team/ |
| SEO audit | marketing-skill/ |
| Literature review | research/ |
| Compliance audit | compliance-os/ |
| Pricing strategy | commercial/ |
| Financial model | finance/ |
| Sprint planning | project-management/ |
| GTM strategy | business-growth/ |

## Version
v2.10.3 — 345 skills, 17 domains, 78 plugins
