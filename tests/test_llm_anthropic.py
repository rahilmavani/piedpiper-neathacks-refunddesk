from refunddesk.llm import LLMClient, ToolCall


def test_anthropic_messages_conversion_without_native_blocks():
    client = object.__new__(LLMClient)
    history = [
        {"role": "user", "content": "refund A1"},
        {"role": "assistant", "content": "checking", "tool_calls": [ToolCall("t1", "lookup_order", '{"order_id": "A1"}')]},
        {"role": "tool_results", "results": [{"tool_call_id": "t1", "name": "lookup_order", "content": "{}", "is_error": True}]},
    ]
    out = client._anthropic_messages(history)
    assert out[1]["content"][1] == {"type": "tool_use", "id": "t1", "name": "lookup_order", "input": {"order_id": "A1"}}
    assert out[2]["content"][0]["is_error"] is True


def test_anthropic_native_blocks_are_replayed():
    client = object.__new__(LLMClient)
    native = [{"type": "text", "text": "hi"}]
    out = client._anthropic_messages([{"role": "assistant", "content": "hi", "native": native, "native_provider": "anthropic"}])
    assert out == [{"role": "assistant", "content": native}]
