---
name: artifacts-builder
description: Creates sophisticated, multi-component HTML artifacts for claude.ai using React 18, TypeScript, Vite, Tailwind CSS, and shadcn/ui. Bundles everything into a single self-contained HTML file. Use when building interactive UI artifacts, dashboards, or demos.
source: https://github.com/ComposioHQ/awesome-claude-skills
---

# Artifacts Builder

## Overview
Build sophisticated, multi-component HTML artifacts for claude.ai using modern frontend technologies. Output is a single self-contained HTML file ready for direct sharing.

## Technology Stack
- React 18 + TypeScript
- Vite (dev server) + Parcel (bundling)
- Tailwind CSS 3.4.1
- shadcn/ui (40+ components)
- Node 18+

## Workflow

1. **Initialize** — Run initialization script to scaffold repo
2. **Develop** — Modify generated code files
3. **Bundle** — Run Parcel to produce single inlined HTML file
4. **Display** — Show artifact to user
5. **Test** — Verify functionality if needed

## Initialization
```bash
# Initialize project
npx create-artifacts-app my-artifact
cd my-artifact
npm install
```

## Development
```bash
# Dev server with hot reload
npm run dev

# Main component: src/App.tsx
# Styles: src/index.css (Tailwind)
```

## Bundling
```bash
# Produces dist/artifact.html — single inlined file
npm run build
```

## Design Principles

### Avoid AI Aesthetic Clichés
- No excessive centered layouts
- No purple gradients as default palette
- No uniform rounded corners everywhere
- No Inter font for everything
- Use appropriate visual hierarchy for the content type

### Component Structure
```tsx
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useState } from 'react'

export default function App() {
  const [count, setCount] = useState(0)
  
  return (
    <div className="min-h-screen bg-background p-8">
      <Card className="max-w-md mx-auto">
        <CardHeader>
          <CardTitle>My Artifact</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="mb-4">Count: {count}</p>
          <Button onClick={() => setCount(c => c + 1)}>
            Increment
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}
```

## Available shadcn/ui Components
accordion, alert, alert-dialog, avatar, badge, button, calendar, card, checkbox, collapsible, command, context-menu, dialog, dropdown-menu, form, hover-card, input, label, menubar, navigation-menu, popover, progress, radio-group, scroll-area, select, separator, sheet, skeleton, slider, switch, table, tabs, textarea, toast, toggle, tooltip

## Output
The bundled `dist/artifact.html` file is fully self-contained — no external dependencies, CDN links, or server required. Paste directly into Claude artifacts or share as a standalone file.
