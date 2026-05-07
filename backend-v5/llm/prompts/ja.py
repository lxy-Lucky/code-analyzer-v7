from __future__ import annotations


def search_prompt(query: str, hits: list[dict]) -> list[dict]:
    parts = []
    for i, h in enumerate(hits, 1):
        sig  = h.get("signature") or h.get("qualified_name", "")
        body = (h.get("body_text") or "").strip()
        parts.append(
            f"[{i}] ファイル: {h.get('file_path', '')} {h.get('start_line', '')}行目\n"
            f"シグネチャ: {sig}\n"
            f"コード:\n{body[:600]}"
        )
    context = "\n\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "あなたはシニアJava/フロントエンドエンジニアで、新しいチームメンバーが"
                "コードベースを理解するのを支援します。取得したコードスニペットに基づいて、"
                "ユーザーの質問に詳しく答えてください：\n"
                "1. 各メソッドの具体的な役割と使用シーンを説明する\n"
                "2. 関連するメソッドが複数ある場合は、違いと適用シーンを明確にする\n"
                "3. 簡単な呼び出し例を示す\n"
                "4. 検索結果が関連していない場合は、見つからなかったと直接伝える\n"
                "日本語で回答し、コード例は元の言語のまま保持してください。"
            ),
        },
        {
            "role": "user",
            "content": f"質問：{query}\n\n取得した関連コード：\n\n{context}",
        },
    ]


def impact_prompt(impact_chains: list[dict]) -> list[dict]:
    parts = []
    for chain in impact_chains:
        changed = chain["changed"]
        parts.append(
            f"変更メソッド: {changed['signature']}\n"
            f"場所: {changed['file_path']} {changed['start_line']}行目"
        )

        nodes    = chain.get("impact_chain", [])
        direct   = [n for n in nodes if n["depth"] == 1]
        indirect = [n for n in nodes if n["depth"] > 1]

        if direct:
            parts.append("直接呼び出し（1層）：")
            for n in direct:
                call_info = f"  {n['call_line']}行目で変更メソッドを呼び出し" if n.get("call_line") else ""
                parts.append(
                    f"  {n['signature']}\n"
                    f"  場所: {n['file_path']} {n['start_line']}行目{call_info}"
                )

        if indirect:
            parts.append("間接呼び出し（2層以上）：")
            for n in indirect[:8]:
                call_info = f"  {n['call_line']}行目で上位メソッドを呼び出し" if n.get("call_line") else ""
                parts.append(
                    f"  [{n['depth']}層] {n['signature']}\n"
                    f"  場所: {n['file_path']} {n['start_line']}行目{call_info}"
                )
        parts.append("")

    context = "\n".join(parts)
    return [
        {
            "role": "system",
            "content": (
                "あなたはコード変更影響分析の専門家です。コールチェーンに基づいて影響を分析し、"
                "以下の3つのセクションで出力してください：\n"
                "## 影響範囲\n"
                "## リスクポイント\n"
                "## テスト推奨事項\n\n"
                "重要な制約：上記で提供されたコールチェーンと行番号のみに基づいて分析してください。"
                "チェーンに含まれていないメソッドやファイルを推測したり言及したりしないでください。"
                "簡潔に要点を絞って、日本語で回答してください。"
            ),
        },
        {
            "role": "user",
            "content": f"変更影響チェーン：\n\n{context}\n影響範囲、リスクポイント、テスト推奨事項を分析してください。",
        },
    ]
