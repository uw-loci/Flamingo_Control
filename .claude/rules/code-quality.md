---
paths:
  - "src/**/*.py"
---

# Error handling and code quality

Loads when you work on Python source.

## Error Handling

New and refactored code that reports a failure to the user or the log raises a `FlamingoError` subclass from `src/py2flamingo/core/errors.py`: `ConnectionError`, `CommandError`, `HardwareError`, `DataError`, `WorkflowError`, `ConfigurationError`, `ValidationError`, `TimeoutError`, `SystemError`. Three of those names shadow Python builtins, so import them explicitly from `py2flamingo.core.errors`.

The constructor is `FlamingoError(message, error_code=None, context=None, cause=None, suggestions=None)`. `context` is a plain dict (where it happened, the value attempted, the valid range). Most subclasses take one extra keyword, such as `component` on `HardwareError`, `command_code` on `CommandError` and `timeout_seconds` on `TimeoutError`. `wrap_external_error()` wraps a standard exception.

Numeric codes are constants on `ErrorCodes` in the same file. Ranges: 1000 connection, 2000 command, 3000 hardware, 4000 data, 5000 workflow, 6000 configuration, 7000 validation, 8000 timeout, 9000 system. Add a new code to the matching range.

Formatting and logging helpers (`ErrorFormatter`, `ErrorLogger`, `get_error_logger()`, `log_error()`, `format_error()`) are in `src/py2flamingo/core/error_formatting.py`; `docs/error_migration_examples.py` has worked examples.

Much of the existing code still raises `ValueError`/`RuntimeError` directly. Replace those with `FlamingoError` subclasses when refactoring that code, and update its tests to expect the new types.

## Code Quality Guidelines

### Avoid Hardcoded Values

Prefer tracking real state over hardcoded timing values: a fixed delay or timeout that happens to work on one rig breaks when conditions change.

**Bad Pattern - Hardcoded Delays:**
```python
# BAD: Magic number that may not work in all conditions
MIN_WORKFLOW_EXECUTION_TIME = 2.0  # What if workflow takes longer to start?

if elapsed < MIN_WORKFLOW_EXECUTION_TIME:
    return  # Ignore early signals
```

**Good Pattern - State Transitions:**
```python
# GOOD: Track actual state changes
self._workflow_running = False

def _on_workflow_started(self, message):
    self._workflow_running = True

def _on_system_idle(self, message):
    if not self._workflow_running:
        return  # Ignore - workflow hasn't started yet
    self._workflow_running = False
    self._completion_event.set()
```

**Principles:**
1. **Use state machines** instead of timing assumptions
2. **Track transitions** (not-running → running → completed)
3. **Query actual state** rather than assuming based on time
4. **Make constants configurable** if they must exist (user settings, not magic numbers)

**Common Violations:**
- Hardcoded delays (`time.sleep(2.0)`)
- Magic timeout values that assume certain performance
- Assumptions about how long operations take
- Fixed retry counts without considering actual conditions

**Exception:** Some hardware protocols have documented timing requirements. These should be:
1. Documented with references to hardware specs
2. Named clearly (`HARDWARE_SETTLE_TIME_MS = 50  # Per datasheet section 4.2`)
3. Placed in a configuration or constants module
