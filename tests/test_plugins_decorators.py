# tests/test_plugins_decorators.py
import pytest
from kernelmcp.plugins import mcp_tool, limit_list


def test_mcp_tool_sets_flag_and_preserves_behavior():
    # simplest: mark a function and keep behavior/name
    @mcp_tool
    def double(x):
        """return x*2"""
        return x * 2

    # attribute present
    assert hasattr(double, "_is_mcp_tool") and double._is_mcp_tool is True

    # behavior unchanged
    assert double(3) == 6

    # name should be preserved
    assert double.__name__ == "double"


def test_limit_list_basic_for_list_and_non_list():
    # limit a list to 3 items; YOU must keep slicing semantics
    @limit_list(max_items=3)
    def make_list():
        return [1, 2, 3, 4, 5]

    # non-list value should pass through unchanged
    @limit_list()  # default max_items=50
    def make_str():
        return "a"*100

    rows = make_list()
    assert rows == [1, 2, 3] and len(rows) == 3

    s = make_str()
    assert s == "a"*100


def test_stack_mcp_tool_then_limit_list_and_reverse():
    """
    Verify stacking behavior in both orders:
    1) @mcp_tool over @limit_list -> flag + slicing + name preserved
    2) @limit_list over @mcp_tool -> flag should still be visible on the outer wrapper
       (requires wraps in limit_list), slicing works, name preserved
    """

    # Order A: mcp_tool outer, limit_list inner
    @mcp_tool
    @limit_list(max_items=2)
    def produce_a():
        return [10, 20, 30, 40]

    assert produce_a.__name__ == "produce_a"
    assert getattr(produce_a, "_is_mcp_tool", False) is True
    assert produce_a() == [10, 20]

    # Order B: limit_list outer, mcp_tool inner
    @limit_list(max_items=2)
    @mcp_tool
    def produce_b():
        return [1, 2, 3]

    # with functools.wraps used in limit_list, both name and custom attrs propagate
    assert produce_b.__name__ == "produce_b"
    assert getattr(produce_b, "_is_mcp_tool", False) is True
    assert produce_b() == [1, 2]

    # sanity: stacking should not affect non-list pass-through
    @limit_list(max_items=2)
    @mcp_tool
    def echo():
        return "ok"

    assert echo.__name__ == "echo"
    assert getattr(echo, "_is_mcp_tool", False) is True
    assert echo() == "ok"
