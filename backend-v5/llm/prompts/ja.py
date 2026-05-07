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
                "あなたはシニアJava/フロントエンドエンジニアで、チームメンバーが"
                "コードベースを理解するのを支援します。取得したコードスニペットに基づいて、"
                "ユーザーの質問に詳しく答えてください：\n"
                "1. 各メソッドのビジネス上の目的、主要ロジック、使用シーンを説明する\n"
                "2. 関連するメソッドが複数ある場合は、違い・呼び出し関係・適用シーンを明確にする\n"
                "3. 重要なパラメータ、戻り値、発生しうる例外を指摘する\n"
                "4. 必要に応じて簡単な使用例を示す\n"
                "5. 検索結果が質問と無関係な場合は、見つからなかったと直接伝える（情報を作り上げない）\n"
                "重要：ユーザーが使用した自然言語と同じ言語で回答する"
                "（日本語の質問→日本語で回答、英語の質問→英語で回答）。"
                "コード例は元の言語のまま保持してください。"
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
                "あなたはコード変更影響分析の専門家です。下記のコールチェーンのみに基づいて分析し、"
                "以下のすべてのセクションを出力してください（省略不可）：\n\n"
                "## 影響範囲\n"
                "影響を受けるメソッドとファイルを列挙し、影響の深さ（直接/間接）を示す。\n\n"
                "## リスク評価\n"
                "各直接呼び出し元にリスクレベル（高/中/低）を付けて理由を説明する。"
                "特に注意する点：インターフェース契約の変化、パラメータ/戻り値の変更、"
                "並行処理・トランザクション境界、NullPointerリスク。\n\n"
                "## テスト推奨事項\n"
                "コールチェーン内の具体的なメソッド名を対象に、実行可能なテスト方針を示す：\n"
                "- 回帰テストが必要なメソッド（正確なメソッド名）\n"
                "- カバーすべき主要シナリオ（正常パス、境界値、異常パス）\n"
                "- 統合テストやE2Eテストが必要かどうか\n\n"
                "重要な制約：上記で提供されたコールチェーンと行番号のみに基づいて分析してください。"
                "チェーンに含まれていないメソッドやファイルを推測したり言及したりしないでください。"
                "簡潔に要点を絞って、日本語で回答してください。"
            ),
        },
        {
            "role": "user",
            "content": f"変更影響チェーン：\n\n{context}\n影響範囲、リスク評価、テスト推奨事項を分析してください。",
        },
    ]
