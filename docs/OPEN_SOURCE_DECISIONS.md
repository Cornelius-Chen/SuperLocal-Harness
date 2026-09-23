# Open-source component decisions

Research date: 2026-09-17. Primary project documentation and repositories were used.

| Component | Useful part | Decision for this MVP |
| --- | --- | --- |
| [OpenHands Agent Canvas](https://github.com/OpenHands/OpenHands) | Self-hosted control center, multiple local/remote backends, automations and ACP-compatible agents | Do not fork now. Strong future UI/backend adapter, but generic agent control is not IRONMAN's authoritative mission/capability semantics. |
| [goose](https://github.com/aaif-goose/goose) | General-purpose Windows/macOS/Linux agent, desktop/CLI/API, many providers and MCP extensions | Preferred future general worker/ACP adapter. Do not copy its runtime into the kernel. |
| [Cline](https://github.com/cline/cline) | IDE/CLI/desktop agent plus an embeddable SDK and human-in-the-loop tooling | Candidate embedded coding-worker adapter after the native gateway is benchmarked. |
| [OpenCode](https://github.com/anomalyco/opencode) | Lightweight coding agent with explicit read-only plan and full-access build agents | Candidate local coding worker. Its plan/build separation informed this MVP's role split. |
| [LiteLLM](https://github.com/BerriAI/litellm) | Unified OpenAI-format gateway, virtual keys, spend tracking, guardrails and load balancing | Supported as an optional endpoint. Add as a service only when several providers or central quotas justify its maintenance cost. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Durable long-running state, human interrupts and memory | Do not adopt yet. The explicit SQLite state machine is easier to audit at current scale; reconsider when branching/recovery complexity exceeds it. |
| [MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic) | Standard resources, prompts and tools with explicit request identifiers | Target tool-adapter boundary. It does not own mission state or authority. |
| [ACP](https://agentclientprotocol.com/get-started/introduction) | Editor/agent interoperability for local and remote agents, including diff-friendly UX | Target worker-adapter boundary so the UI/control plane is not coupled to one agent. |
| [Ollama ChatGPT integration](https://docs.ollama.com/integrations/chatgpt) | Adds selected compatible Ollama models beside native models in the Codex picker | Use for ordinary Codex sessions. Do not rebuild this solved picker problem; the Harness focuses on persistence, governance and experiments. |

## Build / wrap / fork result

- **Build:** thin mission state, event chain, policy broker, Guanlan-specific boundaries and visible router. These are user-specific source-of-truth semantics.
- **Wrap:** DeepSeek/Ollama/LiteLLM through an OpenAI-compatible adapter; later wrap external agents through ACP and tools through MCP.
- **Use as service/tool:** Ollama, LiteLLM and a chosen external worker when they pass local task-family evaluations.
- **Fork:** none in v0.1. Forking would create a large update and security burden before a unique requirement is proven.

Admission of any component requires license review, permissions/data-flow mapping, task-family quality, cost/latency, replaceability, rollback and upstream-health evidence.

