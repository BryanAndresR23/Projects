---
name: ai-marketing-suite
description: Comprehensive marketing analysis platform. Audits websites, generates content, reviews funnels, creates proposals, and develops marketing strategies. Runs parallel subagents for deep analysis. Use with /market commands.
source: https://github.com/zubair-trabzada/ai-marketing-claude
---

# AI Marketing Suite

## Overview
Comprehensive marketing analysis and execution platform for entrepreneurs, agencies, and solopreneurs. Automates website analysis, content generation, funnel audits, proposal creation, and strategy development.

## Commands

| Command | Description |
|---------|-------------|
| `/market audit <url>` | Full website audit (5 parallel agents) |
| `/market quick <url>` | 60-second assessment, no subagents |
| `/market copy <url>` | Rewrite homepage/landing page copy |
| `/market emails <goal>` | Write email sequence |
| `/market social <topic>` | Generate social media content |
| `/market ads <platform>` | Create ad copy and strategy |
| `/market funnel <url>` | Full funnel analysis |
| `/market competitors <industry>` | Competitive landscape analysis |
| `/market landing <offer>` | Design landing page |
| `/market launch <product>` | Go-to-market launch plan |
| `/market proposal <client>` | Client-ready proposal |
| `/market report <period>` | Marketing performance report |
| `/market seo <url>` | SEO analysis and recommendations |
| `/market brand <company>` | Brand voice and positioning analysis |
| `/market strategy <goal>` | Full marketing strategy |

## Full Audit (`/market audit <url>`)

Launches 5 parallel subagents:

1. **Content & Messaging Agent** — Headlines, value prop clarity, copy quality, CTAs
2. **Conversion Optimization Agent** — UX, form friction, trust signals, social proof
3. **Competitive Positioning Agent** — Market differentiation, positioning gaps
4. **Technical SEO Agent** — Speed, crawlability, structured data, mobile
5. **Strategic Opportunities Agent** — Growth levers, quick wins, roadmap

### Marketing Score (0–100)

| Category | Weight |
|----------|--------|
| Content & Messaging | 25% |
| Conversion Optimization | 20% |
| SEO & Discoverability | 20% |
| Competitive Positioning | 15% |
| Brand & Trust | 10% |
| Growth & Strategy | 10% |

## Quick Assessment (`/market quick <url>`)

60-second review, no subagents. Evaluates:
- Headline clarity (does it pass the 5-second test?)
- CTA strength and prominence
- Value proposition distinctiveness
- Trust signals (reviews, logos, guarantees)
- Mobile readiness

## Business Type Detection

The suite automatically identifies business type and tailors analysis:
- **SaaS** — focuses on trial conversion, onboarding, expansion revenue
- **E-commerce** — focuses on product discovery, cart, checkout
- **Agency** — focuses on portfolio, case studies, lead gen
- **Local** — focuses on NAP, GMB, local SEO, reviews
- **Creator** — focuses on audience growth, monetization, email capture
- **Marketplace** — focuses on supply/demand balance, trust, liquidity

## Output Standards

All deliverables:
- Prioritized by revenue impact
- Include concrete examples, not theoretical advice
- Client-ready formatting
- Saved to markdown files with executive summaries
- Include specific next steps with owner/timeline

## Sub-Skills Available

`audit`, `copy`, `emails`, `social`, `ads`, `funnel`, `competitors`, `landing`, `launch`, `proposal`, `report`, `seo`, `brand`, `strategy`
