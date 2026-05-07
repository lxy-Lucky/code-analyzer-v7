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
                "You are a senior Java/frontend engineer helping team members "
                "understand the codebase. Based on the retrieved code snippets, "
                "answer the user's question in detail:\n"
                "1. Explain each method's business purpose, key logic and usage context\n"
                "2. If multiple related methods exist, clarify their differences, "
                "call relationships and when to use each\n"
                "3. Highlight important parameters, return values and possible exceptions\n"
                "4. Provide concise usage examples where helpful\n"
                "5. If results are not relevant to the question, say so directly — do not fabricate\n"
                "Important: reply in the same natural language the user used in their question "
                "(English question → English answer, Chinese question → Chinese answer). "
                "Keep code examples in their original language."
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
                "You are a code change impact analysis expert. Strictly analyze based on the call "
                "chain provided below and output all four sections (omit none):\n\n"
                "## Impact Scope\n"
                "List affected methods and files, note impact depth (direct / indirect).\n\n"
                "## Risk Assessment\n"
                "For each direct caller assign a risk level (High / Medium / Low) with rationale. "
                "Focus on: interface contract changes, parameter/return-type shifts, "
                "concurrency & transaction boundaries, null-pointer risks.\n\n"
                "## Testing Recommendations\n"
                "Give actionable test guidance targeting specific method names from the chain:\n"
                "- Methods requiring regression (exact method names)\n"
                "- Key scenarios to cover (happy path, boundary values, error paths)\n"
                "- Whether integration or end-to-end tests are needed\n\n"
                "Important constraint: Only analyze based on the call chain and line numbers "
                "provided. Do not infer or mention methods or files not present in the chain. "
                "Be concise and focused. Reply in English."
            ),
        },
        {
            "role": "user",
            "content": f"Change impact chain:\n\n{context}\nPlease analyze the impact scope, risk assessment, and testing recommendations.",
        },
    ]
