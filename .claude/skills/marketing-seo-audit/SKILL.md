---
name: marketing-seo-audit
description: Comprehensive SEO audit covering crawlability, technical foundations, on-page optimization, content quality, and authority. Produces prioritized action plans with impact ratings. Use when diagnosing ranking problems, traffic drops, or preparing for SEO improvements.
source: https://github.com/coreyhaines31/marketingskills
---

# SEO Audit Agent

## Overview
Comprehensive SEO auditor. Identifies and prioritizes issues across crawlability, technical foundations, on-page optimization, content quality, and link authority.

## Context Gathering (ask first)

1. Site type and industry
2. Business goals and primary KPIs
3. Known issues or recent changes (migrations, redesigns, algorithm updates)
4. Current organic traffic baseline (Search Console data if available)
5. Priority keywords or pages
6. Analytics/Search Console access

## Audit Framework (Priority Order)

### 1. Crawlability & Indexation
- `robots.txt` — blocking important pages?
- XML sitemap — complete, submitted, no errors?
- Site architecture — crawl depth, orphan pages?
- Crawl budget — pagination, faceted navigation, duplicate URLs?
- Index status — `site:domain.com` count vs actual pages
- Noindex tags — intentional or accidental?
- Canonical tags — pointing correctly?
- Redirect chains — 301 chains adding latency?

### 2. Technical Foundations
**Core Web Vitals** (target thresholds):
- LCP (Largest Contentful Paint): < 2.5s
- INP (Interaction to Next Paint): < 200ms
- CLS (Cumulative Layout Shift): < 0.1

**Additional Checks:**
- Mobile responsiveness (Google Mobile-Friendly Test)
- HTTPS + valid SSL certificate
- Page speed (PageSpeed Insights — mobile and desktop)
- Structured data (use Rich Results Test — NOT `web_fetch`, which misses JS-injected schema)

### 3. On-Page Optimization
- Title tags: unique, 50–60 chars, primary keyword near front
- Meta descriptions: unique, 150–160 chars, includes CTA
- H1: one per page, matches search intent
- Heading hierarchy: logical H1 → H2 → H3
- Internal linking: contextual links, descriptive anchor text
- Image alt text: descriptive, keyword where natural
- URL structure: short, readable, keyword-included

### 4. Content Quality (E-E-A-T)
- Experience: first-hand knowledge signals
- Expertise: author credentials, depth of coverage
- Authoritativeness: brand mentions, citations
- Trustworthiness: accurate info, updated dates, sources cited

### 5. Authority & Links
- Backlink profile quality (DA/DR of linking domains)
- Toxic links (spammy patterns)
- Anchor text distribution
- Internal link equity distribution

## Known Detection Limitations

> **Schema markup**: `web_fetch` and `curl` strip `<script>` tags and cannot detect JavaScript-injected JSON-LD. Use [Google's Rich Results Test](https://search.google.com/test/rich-results) or Screaming Frog for accurate schema validation.

## Output Format

```markdown
## Executive Summary
[2-3 sentence overview of health and priorities]

## Critical Issues (Fix immediately)
1. [Issue] — Impact: HIGH
   Evidence: [specific finding]
   Fix: [exact action]

## Quick Wins (Fix this week)
...

## Strategic Improvements (Roadmap)
...

## Monitoring Checklist
- [ ] Set up Search Console alerts
- [ ] Track Core Web Vitals in CrUX
- [ ] Monthly crawl with Screaming Frog
```

## International SEO Checks
When site has multiple languages/regions:
- Hreflang implementation correct?
- Self-referencing canonicals on each language version?
- Consistent NAP (name, address, phone) for local SEO?
