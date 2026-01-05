# Prompt: Improve Aleph Documentation

Use this prompt to rewrite Aleph's documentation to be more accessible, research-grounded, and less Claude-centric.

---

## Prompt

```
I need you to improve the documentation for Aleph, an MCP server for recursive LLM reasoning over documents.

**Goals:**
1. Make it accessible to developers who aren't familiar with MCP or recursive reasoning
2. Ground it in the research (RLM paper) without being overly academic
3. Remove Claude-specific assumptions - this works with any MCP-compatible AI
4. Focus on practical "how to use it" over implementation details
5. Don't overstate capabilities or use hype language

**Files to review and potentially update:**
- README.md
- docs/1-4-update.md (implementation plan for sub_query feature)
- CLAUDE.md (should this be renamed?)

**Tone guidelines:**
- Be direct and practical, not salesy
- Use concrete examples over abstract descriptions
- Acknowledge limitations honestly
- Write for a developer audience, not marketing

**Structure suggestions for README:**

1. **One-liner**: What it does in plain English
2. **When to use it**: Clear use cases (long docs, need citations, iterative analysis)
3. **When NOT to use it**: Short docs, latency-critical, etc.
4. **Quick start**: Minimal steps to get running
5. **How it works**: Brief technical explanation with diagram
6. **Tools reference**: What each MCP tool does
7. **sub_query (RLM mode)**: Explain recursive sub-agent calls
8. **Configuration**: Environment variables, CLI flags
9. **Research background**: Link to RLM paper, explain the core insight

**Key messaging to convey:**
- Aleph treats context as an external environment variable (not stuffed into prompts)
- The AI iteratively explores with search, peek, code execution
- sub_query enables RLM-style recursive reasoning (chunk → query sub-agent → aggregate)
- Works with Claude, GPT, Qwen, any MCP-compatible host
- CLI backends (claude, codex) need no API key; API fallback uses Mimo (free)

**Things to remove or tone down:**
- Excessive emoji
- Marketing language ("powerful", "revolutionary", etc.)
- Claude-specific instructions that don't apply to other hosts
- Implementation details that users don't need

**Research context to include:**
The RLM paper (arXiv:2512.24601) showed that:
- LLMs struggle with long contexts even within their window ("context rot")
- Treating prompts as external environment variables enables 10M+ token handling
- Recursive sub-LM calls let the model decompose and aggregate
- This approach is task-agnostic and cost-comparable to base model calls

Please review the current docs, propose specific changes, and implement them. Focus on making Aleph approachable for a developer who just wants to analyze long documents with AI.
```

---

## What this accomplishes

- Makes Aleph accessible to non-Claude users
- Grounds marketing in actual research findings
- Focuses on practical usage over hype
- Creates clear documentation structure
