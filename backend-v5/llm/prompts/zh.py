from __future__ import annotations


def search_prompt(query: str, hits: list[dict]) -> list[dict]:
    parts = []
    for i, h in enumerate(hits, 1):
        sig  = h.get("signature") or h.get("qualified_name", "")
        body = (h.get("body_text") or "").strip()
        parts.append(
            f"[{i}] 文件: {h.get('file_path', '')} 第{h.get('start_line', '')}行\n"
            f"签名: {sig}\n"
            f"代码:\n{body[:600]}"
        )
    context = "\n\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "你是一个资深Java/前端开发工程师，专门帮新员工理解现有代码库。\n"
                "根据检索到的代码片段，详细回答用户的问题。要求：\n"
                "1. 逐条说明每个方法的具体作用和使用场景\n"
                "2. 如果有多个相关方法，说清楚它们的区别和各自适用情况\n"
                "3. 给出简单的调用示例\n"
                "4. 如果检索结果不相关，直接说明没有找到\n"
                "回答用中文，代码示例保留原语言。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{query}\n\n检索到的相关代码：\n\n{context}",
        },
    ]


def impact_prompt(impact_chains: list[dict]) -> list[dict]:
    """问题2修复：每个节点加 call_line，精确到调用行号，约束 LLM 不乱推断。"""
    parts = []
    for chain in impact_chains:
        changed = chain["changed"]
        parts.append(
            f"变更方法: {changed['signature']}\n"
            f"位置: {changed['file_path']} 第{changed['start_line']}行"
        )

        nodes   = chain.get("impact_chain", [])
        direct  = [n for n in nodes if n["depth"] == 1]
        indirect = [n for n in nodes if n["depth"] > 1]

        if direct:
            parts.append("直接调用（1层）：")
            for n in direct:
                call_info = f"  在第 {n['call_line']} 行调用了变更方法" if n.get("call_line") else ""
                parts.append(
                    f"  {n['signature']}\n"
                    f"  位置: {n['file_path']} 第{n['start_line']}行{call_info}"
                )

        if indirect:
            parts.append("间接调用（2层以上）：")
            for n in indirect[:8]:
                call_info = f"  在第 {n['call_line']} 行调用了上层方法" if n.get("call_line") else ""
                parts.append(
                    f"  [{n['depth']}层] {n['signature']}\n"
                    f"  位置: {n['file_path']} 第{n['start_line']}行{call_info}"
                )
        parts.append("")

    context = "\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "你是代码变更影响分析专家。根据调用链分析变更影响，输出三个部分：\n"
                "## 影响范围\n"
                "## 风险点\n"
                "## 测试建议\n\n"
                "重要约束：只根据上方提供的调用链和行号进行分析，"
                "不要推断或提及未出现在调用链中的方法或文件。"
                "语言简洁，重点突出，用中文回答。"
            ),
        },
        {
            "role": "user",
            "content": f"变更影响链：\n\n{context}\n请分析影响范围、风险点和测试建议。",
        },
    ]
