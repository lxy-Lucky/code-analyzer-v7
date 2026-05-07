from __future__ import annotations


def search_prompt(query: str, hits: list[dict]) -> list[dict]:
    parts = []
    for i, h in enumerate(hits, 1):
        sig  = h.get("signature") or h.get("qualified_name", "")
        body = (h.get("body_text") or "").strip()
        parts.append(
            f"[{i}] File: {h.get('file_path', '')} line {h.get('start_line', '')}\n"
            f"Signature: {sig}\n"
            f"Code:\n{body[:600]}"
        )
    context = "\n\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "You are a senior Java/frontend engineer helping new team members "
                "understand the codebase. Based on the retrieved code snippets, "
                "answer the user's question in detail:\n"
                "1. Explain each method's purpose and usage\n"
                "2. Clarify differences between related methods\n"
                "3. Provide simple usage examples\n"
                "4. If results are not relevant, say so directly\n"
                "Reply in English. Keep code examples in their original language."
            ),
        },
        {
            "role": "user",
            "content": f"Question: {query}\n\nRetrieved code:\n\n{context}",
        },
    ]


def impact_prompt(impact_chains: list[dict]) -> list[dict]:
    parts = []
    for chain in impact_chains:
        changed = chain["changed"]
        parts.append(
            f"Changed method: {changed['signature']}\n"
            f"Location: {changed['file_path']} line {changed['start_line']}"
        )

        nodes    = chain.get("impact_chain", [])
        direct   = [n for n in nodes if n["depth"] == 1]
        indirect = [n for n in nodes if n["depth"] > 1]

        if direct:
            parts.append("Direct callers (depth 1):")
            for n in direct:
                call_info = f"  calls changed method at line {n['call_line']}" if n.get("call_line") else ""
                parts.append(
                    f"  {n['signature']}\n"
                    f"  Location: {n['file_path']} line {n['start_line']}{call_info}"
                )

        if indirect:
            parts.append("Indirect callers (depth 2+):")
            for n in indirect[:8]:
                call_info = f"  calls upstream at line {n['call_line']}" if n.get("call_line") else ""
                parts.append(
                    f"  [depth {n['depth']}] {n['signature']}\n"
                    f"  Location: {n['file_path']} line {n['start_line']}{call_info}"
                )
        parts.append("")

    context = "\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "You are a code change impact analysis expert. "
                "Analyze the impact based on the call chain and output three sections:\n"
                "## Impact Scope\n"
                "## Risk Points\n"
                "## Testing Recommendations\n\n"
                "Important constraint: Only analyze based on the call chain and line numbers "
                "provided above. Do not infer or mention methods or files not present in the chain. "
                "Be concise and focused. Reply in English."
            ),
        },
        {
            "role": "user",
            "content": f"Change impact chain:\n\n{context}\nPlease analyze the impact scope, risks, and testing suggestions.",
        },
    ]
