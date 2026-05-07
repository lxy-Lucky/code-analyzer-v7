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
                "你是一个资深Java/前端开发工程师，专门帮团队成员理解现有代码库。\n"
                "根据检索到的代码片段，详细回答用户的问题。要求：\n"
                "1. 逐条说明每个方法的具体作用、业务含义和使用场景\n"
                "2. 如果有多个相关方法，说清楚它们的区别、调用关系和各自适用情况\n"
                "3. 指出关键参数、返回值和可能抛出的异常\n"
                "4. 给出简单的调用示例（如有必要）\n"
                "5. 如果检索结果与问题不相关，直接说明未找到，不要编造\n"
                "重要：用与用户提问相同的自然语言回答（中文问题用中文答，英文问题用英文答），"
                "代码示例保留原语言。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{query}\n\n检索到的相关代码：\n\n{context}",
        },
    ]


def impact_prompt(impact_chains: list[dict]) -> list[dict]:
    parts = []
    for chain in impact_chains:
        changed = chain["changed"]
        parts.append(
            f"变更方法: {changed['signature']}\n"
            f"位置: {changed['file_path']} 第{changed['start_line']}行"
        )

        nodes    = chain.get("impact_chain", [])
        direct   = [n for n in nodes if n["depth"] == 1]
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
                "你是代码变更影响分析专家。严格根据下方提供的调用链分析变更影响，"
                "按以下格式输出（不要遗漏任何部分）：\n\n"
                "## 影响范围\n"
                "列出受影响的方法和文件，说明影响层级（直接/间接）。\n\n"
                "## 风险评估\n"
                "对每个直接调用方，给出风险等级（高/中/低）并说明理由，"
                "重点关注：接口契约变化、参数/返回值变更、并发与事务边界、空指针风险。\n\n"
                "## 测试建议\n"
                "针对影响链中具体的方法名，给出可操作的测试方案，包括：\n"
                "- 需要回归的具体方法（精确到方法名）\n"
                "- 需要覆盖的关键场景（正常路径、边界值、异常路径）\n"
                "- 是否需要集成测试或端到端测试\n\n"
                "重要约束：只根据上方提供的调用链和行号进行分析，"
                "不要推断或提及调用链中未出现的方法或文件。"
                "语言简洁，重点突出，用中文回答。"
            ),
        },
        {
            "role": "user",
            "content": f"变更影响链：\n\n{context}\n请分析影响范围、风险评估和测试建议。",
        },
    ]
