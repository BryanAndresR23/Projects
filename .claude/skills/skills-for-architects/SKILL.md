---
name: skills-for-architects
description: AEC (Architecture, Engineering, Construction) professional toolkit with 39 skills across 10 plugins covering due diligence, site planning, zoning analysis, programming, sustainability, materials research, presentations, and project dossier management.
source: https://github.com/AlpacaLabsLLC/skills-for-architects
---

# Architecture Studio

## Overview
Claude plugin system for AEC professionals. Contains 7 agents, 39 skills, and 7 rules across 10 installable plugins. Activate via `/studio` command.

## Plugin Index

| Plugin | Skills | Description |
|--------|--------|-------------|
| **00-due-diligence** | 7 | NYC property research: landmarks, permits, violations, ACRIS, HPD, BSA |
| **01-site-planning** | 4 | Environmental, mobility, demographic, and historical analysis |
| **02-zoning-analysis** | 2 | Buildable envelope calculations, 3D visualization (NYC) |
| **03-programming** | 2 | Workplace strategy and occupancy compliance |
| **04-specifications** | 1 | CSI-formatted outline specifications |
| **05-sustainability** | 4 | EPD parsing, research, environmental impact comparison |
| **06-materials-research** | 12 | Product research, spec extraction, FF&E scheduling |
| **07-presentations** | 3 | Slide deck generation, color palettes, image processing |
| **08-dispatcher** | 2 + hooks | Router, help system, event-driven workflows |
| **09-project-dossier** | 2 | Persistent project documentation and decision records |

## Agents

| Agent | Role |
|-------|------|
| site-planner | Site analysis and planning |
| nyc-zoning-expert | NYC zoning code and buildable envelope |
| workplace-strategist | Occupancy and space programming |
| product-and-materials-researcher | Materials and product specification |
| ffe-designer | Furniture, fixtures, and equipment |
| sustainability-specialist | EPD analysis and environmental compliance |
| brand-manager | Presentation and visual identity |

## Core Commands

```
/studio                    — Route to appropriate agent or skill
/studio help               — List available capabilities
/studio due-diligence      — Start property research workflow
/studio zoning <address>   — Calculate buildable envelope
/studio spec <system>      — Generate CSI specification
/studio materials <type>   — Research and compare materials
/studio ffe <room-type>    — Generate FF&E schedule
/studio dossier            — Open/update project record
```

## Rules (Enforced Globally)

1. **Units**: Always specify units (SF, SM, LF, etc.)
2. **Code Citations**: Cite specific code section numbers
3. **CSI Formatting**: Use MasterFormat section numbers
4. **Professional Disclaimer**: Recommend licensed professional review for code compliance
5. **Metric/Imperial**: Offer both when relevant
6. **Source Attribution**: Credit reference documents
7. **Uncertainty**: Flag assumptions explicitly

## Project Dossier
Persistent project state tracking across sessions:
```markdown
# Project: [Name]
**Address**: 
**Client**: 
**Zoning District**: 
**Key Decisions**: 
**Open Questions**: 
**Next Steps**: 
```

The dispatcher agent maintains this file and updates it after each significant decision or finding.

## NYC-Specific Capabilities
- Zoning Resolution interpretation (ZR 2016)
- ACRIS title search guidance
- DOB permit history lookup
- Landmarks Preservation Commission (LPC) research
- HPD violation history
- BSA (Board of Standards & Appeals) precedent research

## Version Notes
v1.2 introduced native subagents and persistent project state via Project Dossier plugin.
