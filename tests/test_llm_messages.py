from refunddesk.llm import LLMClient, ToolCall


def test_openai_messages_conversion():
    history = [
        {"role": "user", "content": "refund A1"},
        {"role": "assistant", "content": "", "tool_calls": [ToolCall("c1", "lookup_order", '{"order_id": "A1"}')]},
        {"role": "tool_results", "results": [{"tool_call_id": "c1", "name": "lookup_order", "content": "{}", "is_error": False}]},
    ]
    out = LLMClient._openai_messages("SYS", history)
    assert out[0] == {"role": "system", "content": "SYS"}
    assert out[2]["tool_calls"][0]["function"]["name"] == "lookup_order"
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
