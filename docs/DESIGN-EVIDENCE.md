# Design evidence

The manager-style council follows the distinction between specialists and a synthesizing manager in OpenAI orchestration guidance: <https://developers.openai.com/api/docs/guides/agents/orchestration>.

Guardrails and approvals belong next to consequential actions, and workflow behavior needs repeatable evals: <https://developers.openai.com/api/docs/guides/agents/guardrails-approvals> and <https://developers.openai.com/api/docs/guides/agent-evals>.

Multi-agent debate can improve some tasks but can converge on a confident wrong answer and plateau: <https://arxiv.org/abs/2305.14325>. Therefore four perspectives are a configurable hypothesis, not proof; evidence, criteria, hard gates, and bounded loops remain authoritative.

Iterative feedback can improve results in Self-Refine and Reflexion-style workflows, but this repository still requires baseline/candidate evaluations: <https://arxiv.org/abs/2303.17651> and <https://arxiv.org/abs/2303.11366>.

Governance follows the NIST AI RMF principle of evidence, controls, monitoring, and accountability: <https://www.nist.gov/itl/ai-risk-management-framework>.
