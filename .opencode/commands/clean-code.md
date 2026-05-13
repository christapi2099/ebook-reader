---
description: Apply Clean Code principles (Robert C. Martin) to the given task
---

Apply Clean Code principles (Robert C. Martin) to the following task. Every line you write or modify must satisfy these rules.

## Task

$ARGUMENTS

---

## Rules to Apply

### Naming
- Names reveal intent. If a name needs a comment, the name is wrong.
- No single-letter variables outside loop counters in short scopes.
- Name length proportional to scope size. Module-level names are descriptive; local temps can be short.
- Use searchable names. Replace magic numbers with named constants.
- Functions/methods: verb or verb phrase (`calculate_pay`, `delete_page`, `save`).
- Classes/models: noun or noun phrase (`Customer`, `MembershipPlan`). Never `Manager`, `Processor`, `Data`, `Info`.
- One word per concept consistently. Don't mix `fetch`, `retrieve`, `get` for the same abstraction.
- Use domain vocabulary: CS terms for technical code, problem-domain terms for business logic.
- Don't encode type or scope into names. No `str_name`, `lst_items`, `m_field`.

### Functions
- Small. A function body should rarely exceed 20 lines.
- Do one thing. If you can extract a meaningful sub-function, the original did too much.
- One level of abstraction per function. Don't mix high-level orchestration with low-level detail.
- Stepdown rule: read top-to-bottom like a narrative. Callers above callees.
- Prefer fewer arguments. 0-2 ideal. 3+ usually means the args should be a dataclass, dict, or object.
- No flag/boolean arguments. They advertise the function does two things. Split into two functions.
- No side effects. If named `check_password`, don't also initialize a session.
- Command-query separation: either change state OR return information, not both.
- Prefer raising exceptions over returning error codes or None.
- Extract try/except bodies into their own functions. Error handling is one thing.

### Comments
- Don't comment bad code — rewrite it.
- Good comments: legal notices, intent explanation, clarification of obscure APIs, TODO with ticket ref, warning of consequences.
- Bad comments: redundant narration, journal/changelog, closing-brace markers, commented-out code, docstrings on obvious private helpers.
- If you can express it with a well-named variable or function, don't use a comment.

### Formatting
- Newspaper metaphor: high-level summary at top, details increase downward.
- Blank lines separate concepts. Related lines stay dense with no gaps.
- Vertical distance: declare variables close to usage. Keep related functions vertically near each other.
- Caller above callee in the file.
- Lines under 120 chars. Prefer shorter.
- Consistent style always beats personal preference. Follow the project's existing conventions.

### Objects and Data Structures
- Objects hide data, expose behavior. Data structures expose data, have no behavior. Don't create hybrids.
- Law of Demeter: talk to friends, not strangers. No chained accessor calls like `a.get_b().get_c().do()`.
- For Django: models are Active Records (data structures + persistence). Keep business logic in service layers, not model methods that grow unbounded.
- Don't reflexively add getters/setters. Use Python properties only when you need a computation or guard.

### Error Handling
- Raise exceptions, don't return None or error codes.
- Provide context in exceptions: what operation failed and why.
- Don't return None. Return empty collections, raise exceptions, or use sentinel objects.
- Don't pass None as an argument.
- Write the try/except first when a function could fail. It defines the contract.
- Wrap third-party exceptions into your own types at system boundaries.
- Separate business logic from error handling — keep try blocks small.

### Boundaries
- Wrap third-party APIs behind your own interface. One place to change when the lib updates.
- Don't leak third-party types across your codebase. Contain them at the boundary.
- Write learning tests for unfamiliar APIs to lock in your understanding.

### Tests
- FIRST: Fast, Independent, Repeatable, Self-validating, Timely.
- One concept per test. Test name describes the scenario and expected outcome.
- Tests are as important as production code. Keep them clean, refactored, readable.
- Build domain-specific test helpers to reduce duplication and improve clarity.
- Test boundary conditions exhaustively. Bugs cluster at edges.
- When a bug is found, write a test that exposes it before fixing.

### Classes
- Single Responsibility Principle: one reason to change.
- Small and focused. A class with 10+ public methods or 300+ lines needs splitting.
- High cohesion: most methods use most instance variables. If a subset of methods only uses a subset of fields, that's a second class trying to escape.
- Open/Closed Principle: extend behavior without modifying existing code. Prefer composition and polymorphism.
- Dependency Inversion: depend on abstractions (protocols/ABCs), not concretions.

### Simple Design (Emergence)
In priority order:
1. Runs all the tests (correctness first).
2. No duplication. DRY — but don't over-abstract. Three instances of duplication justify extraction.
3. Expressive. Code reads clearly to the next developer.
4. Minimal. No unused code, speculative generality, or dead abstractions.

### Code Smells — Quick Checklist
- Redundant or obsolete comments
- Dead code or dead functions — delete, don't comment out
- Duplication (copy-paste or structural)
- Feature envy — method uses another object's data more than its own
- Artificial coupling — things grouped together that don't belong together
- Hidden temporal coupling — make execution order explicit via args or return values
- Inconsistency — if you do it one way, do similar things the same way everywhere
- Clutter — unused imports, default constructors, unused variables
- Magic numbers — use named constants
- Negative conditionals — prefer `if is_valid` over `if not is_invalid`
- Functions with too many arguments
- Selector/flag arguments
- Misplaced responsibility — put code where a reader would expect it
- Code at wrong level of abstraction

## How to Apply

1. Read the target code thoroughly before changing anything.
2. Apply the rules above to every line you write or modify.
3. Boy Scout Rule: leave code cleaner than you found it, but only in files you're already modifying.
4. Run tests after changes. Never break existing behavior.
5. Refactoring is iterative — small, verified steps. Don't rewrite the world.
