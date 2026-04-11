"""
Complete integration test for state machine with agents
"""
import sys
sys.path.insert(0, '/home/lixujun/AICosmos')

# Load state machine
exec(open('app/agent/state_machine.py').read())

print("=" * 60)
print("STATE MACHINE INTEGRATION TEST SUITE")
print("=" * 60)

# Test 1: Basic StateMachine functionality
print("\n[Test 1] Basic StateMachine")
sm = StateMachine(
    initial_state='INIT',
    states={
        'INIT': TransitionRule(['action1'], 'NEXT'),
        'NEXT': TransitionRule(['action2'], 'DONE'),
        'DONE': TransitionRule(['terminate'], 'DONE')
    }
)
assert sm.current_state == 'INIT'
assert sm.validate_action('action1', {}) is None
assert sm.validate_action('action2', {}) is not None
sm.advance('action1', {}, 'OK')
assert sm.current_state == 'NEXT'
print("✓ Basic state machine works")

# Test 2: Action-based routing (tool:action format)
print("\n[Test 2] Action-based routing")
sm2 = StateMachine(
    initial_state='START',
    states={
        'START': TransitionRule(['tool:act1', 'tool:act2'], 'END'),
        'END': TransitionRule(['*'], 'END')
    }
)
assert sm2.validate_action('tool', {'action': 'act1'}) is None
assert sm2.validate_action('tool', {'action': 'act3'}) is not None
assert sm2.validate_action('other_tool', {'action': 'act1'}) is not None
print("✓ Action-based routing works")

# Test 3: Dynamic state transitions
print("\n[Test 3] Dynamic transitions")
def dynamic_router(tool, kwargs, res):
    if 'PASS' in res:
        return 'SUCCESS'
    elif 'FAIL' in res:
        return 'RETRY'
    return 'UNKNOWN'

sm3 = StateMachine(
    initial_state='TEST',
    states={
        'TEST': TransitionRule(['run'], dynamic_router),
        'SUCCESS': TransitionRule(['*'], 'SUCCESS'),
        'RETRY': TransitionRule(['*'], 'TEST'),
        'UNKNOWN': TransitionRule(['*'], 'UNKNOWN')
    }
)
sm3.advance('run', {}, 'PASS')
assert sm3.current_state == 'SUCCESS'
sm3.current_state = 'TEST'
sm3.advance('run', {}, 'FAIL')
assert sm3.current_state == 'RETRY'
print("✓ Dynamic transitions work")

# Test 4: Error handling
print("\n[Test 4] Error handling")
sm4 = StateMachine(
    initial_state='A',
    states={
        'A': TransitionRule(['tool'], 'B'),
        'B': TransitionRule(['*'], 'B')
    }
)
sm4.advance('tool', {}, 'Error: something')
assert sm4.current_state == 'A'
sm4.advance('tool', {}, None)
assert sm4.current_state == 'A'
sm4.advance('tool', {}, 'Success')
assert sm4.current_state == 'B'
print("✓ Error handling works")

# Test 5: Gardener workflow simulation
print("\n[Test 5] Gardener workflow simulation")
gardener = StateMachine(
    initial_state="INIT",
    states={
        "INIT": TransitionRule(
            ["skill_gardener_tool:inspect_trace_map", "skill_gardener_tool:check_upstream_updates"],
            "PLANNING"
        ),
        "PLANNING": TransitionRule(
            ["skill_gardener_tool:generate_refactor_plan"],
            "CODING"
        ),
        "CODING": TransitionRule(
            ["skill_gardener_tool:execute_refactor"],
            "TESTING"
        ),
        "TESTING": TransitionRule(
            ["skill_gardener_tool:test_skill"],
            lambda t, k, r: "DONE" if "PASSED" in r else "PLANNING"
        ),
        "DONE": TransitionRule(["terminate"], "DONE")
    }
)

# Simulate workflow
gardener.advance("skill_gardener_tool", {"action": "inspect_trace_map"}, "Trace loaded")
assert gardener.current_state == "PLANNING"

gardener.advance("skill_gardener_tool", {"action": "generate_refactor_plan"}, "Plan created")
assert gardener.current_state == "CODING"

gardener.advance("skill_gardener_tool", {"action": "execute_refactor"}, "Code generated")
assert gardener.current_state == "TESTING"

# Test failed -> retry
gardener.advance("skill_gardener_tool", {"action": "test_skill"}, "FAILED")
assert gardener.current_state == "PLANNING"

# Redo workflow
gardener.advance("skill_gardener_tool", {"action": "generate_refactor_plan"}, "Plan v2")
gardener.current_state = "CODING"
gardener.advance("skill_gardener_tool", {"action": "execute_refactor"}, "Code v2")
gardener.advance("skill_gardener_tool", {"action": "test_skill"}, "PASSED")
assert gardener.current_state == "DONE"

print("✓ Gardener workflow simulation works")

# Test 6: Reviewer workflow simulation
print("\n[Test 6] Reviewer workflow simulation")
reviewer = StateMachine(
    initial_state="INIT",
    states={
        "INIT": TransitionRule(
            ["skill_reviewer_tool:view_skill_code", "skill_reviewer_tool:list_test_cases"],
            "CODING_TESTS"
        ),
        "CODING_TESTS": TransitionRule(
            ["skill_reviewer_tool:add_test_case", "skill_reviewer_tool:run_pytest"],
            lambda t, k, r: "REPORTING" if k.get('action') == "run_pytest" else "CODING_TESTS"
        ),
        "REPORTING": TransitionRule(
            ["skill_reviewer_tool:submit_report"],
            "DONE"
        ),
        "DONE": TransitionRule(["terminate"], "DONE")
    }
)

reviewer.advance("skill_reviewer_tool", {"action": "view_skill_code"}, "Code shown")
assert reviewer.current_state == "CODING_TESTS"

reviewer.advance("skill_reviewer_tool", {"action": "add_test_case"}, "Test 1 added")
assert reviewer.current_state == "CODING_TESTS"

reviewer.advance("skill_reviewer_tool", {"action": "add_test_case"}, "Test 2 added")
assert reviewer.current_state == "CODING_TESTS"

reviewer.advance("skill_reviewer_tool", {"action": "run_pytest"}, "All tests passed")
assert reviewer.current_state == "REPORTING"

reviewer.advance("skill_reviewer_tool", {"action": "submit_report"}, "Report saved")
assert reviewer.current_state == "DONE"

print("✓ Reviewer workflow simulation works")

# Test 7: State history tracking
print("\n[Test 7] State history tracking")
sm7 = StateMachine(
    initial_state='A',
    states={
        'A': TransitionRule(['t1'], 'B'),
        'B': TransitionRule(['t2'], 'C'),
        'C': TransitionRule(['t3'], 'D'),
        'D': TransitionRule(['*'], 'D')
    }
)
assert sm7.history == ['A']
sm7.advance('t1', {}, 'OK')
assert sm7.history == ['A', 'B']
sm7.advance('t2', {}, 'OK')
assert sm7.history == ['A', 'B', 'C']
sm7.advance('t3', {}, 'OK')
assert sm7.history == ['A', 'B', 'C', 'D']
print("✓ State history tracking works")

print("\n" + "=" * 60)
print("✅ ALL INTEGRATION TESTS PASSED!")
print("=" * 60)
