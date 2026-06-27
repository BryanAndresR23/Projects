---
name: mcp-builder
description: Guides building high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools. Use when creating new MCP servers or integrating external APIs as MCP tools.
source: https://github.com/ComposioHQ/awesome-claude-skills
---

# MCP Server Builder

## Overview
Build high-quality MCP (Model Context Protocol) servers that enable LLMs to interact with external services through well-designed tools.

## Four-Phase Development Process

### Phase 1: Research & Planning
- Study agent-centric design principles — workflows over API endpoints
- Review MCP protocol documentation
- Exhaustively study the target API documentation
- Create detailed implementation plan: tool selection, error handling, input/output design

### Phase 2: Implementation
**Project Structure**
```
my-mcp-server/
├── src/
│   ├── index.ts (or main.py)
│   ├── tools/
│   └── helpers/
├── package.json (or pyproject.toml)
└── README.md
```

**Core Implementation Principles**
- Consolidate related operations into single tools (avoid tool proliferation)
- Design for constrained context windows — lean schemas, clear descriptions
- Actionable error messages — tell the LLM how to recover
- Follow natural task workflows, not API structure

**Tool Annotations**
- `readOnly: true` for read operations
- `destructive: true` for deletes/overwrites
- `idempotent: true` for safe-to-retry operations

### Phase 3: Review & Refine
- DRY principles and consistency check
- Test safely using evaluation harness (avoid direct execution during development)
- Apply language-specific quality checklists

### Phase 4: Evaluation
- Create 10 independent, complex, read-only evaluation questions
- Verify answers personally before inclusion
- Output results in standardized XML format

## Key Design Principles

1. **Agent-centric**: Design for workflows, not API mirroring
2. **Context-efficient**: Minimize tokens in tool descriptions and schemas
3. **Error recovery**: Every error should tell the LLM what to do next
4. **Idempotency**: Design tools to be safely retried

## Python Template
```python
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

server = Server("my-server")

@server.list_tools()
async def list_tools():
    return [
        Tool(
            name="get_data",
            description="Retrieves data by ID. Returns JSON object with fields: id, name, value.",
            inputSchema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "The resource ID"}
                },
                "required": ["id"]
            }
        )
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "get_data":
        # implementation
        pass

async def main():
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())
```

## TypeScript Template
```typescript
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const server = new Server({ name: "my-server", version: "1.0.0" });

server.setRequestHandler(ListToolsRequestSchema, async () => ({
  tools: [{
    name: "get_data",
    description: "...",
    inputSchema: { type: "object", properties: { id: { type: "string" } }, required: ["id"] }
  }]
}));

const transport = new StdioServerTransport();
await server.connect(transport);
```
