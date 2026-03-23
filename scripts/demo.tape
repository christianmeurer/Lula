# Lula Agent Demo — VHS tape script (Windows cmd.exe compatible)
# Generate with: vhs scripts/demo.tape
# Requires: https://github.com/charmbracelet/vhs
#
# Every visible line is a valid cmd.exe `echo` command so VHS
# can execute it without "is not recognized" errors.

Output docs/demo.gif

Set Width 1200
Set Height 600
Set FontSize 14
Set Theme "Dracula"
Set TypingSpeed 40ms

Sleep 500ms

Type "echo # Lula -- autonomous coding agent demo"
Enter
Sleep 800ms

Type "echo cd py ^&^& uv run python -m lg_orch run --task 'Fix the off-by-one error in pagination logic' --repo ../demo-app"
Enter
Sleep 1500ms

# -- Ingest phase --
Set TypingSpeed 0ms

Type "echo [ingest] Loading repository ../demo-app"
Enter
Sleep 400ms
Type "echo   repo: demo-app  files: 47  lang: python  size: 128 KB"
Enter
Sleep 300ms
Type "echo   SCIP index: 312 symbols resolved"
Enter
Sleep 500ms

# -- Router phase --
Type "echo [router] Classifying task..."
Enter
Sleep 400ms
Type "echo   route: interactive  confidence: 0.91  model: gpt-4o"
Enter
Sleep 500ms

# -- Planner phase --
Type "echo [planner] Generating execution plan"
Enter
Sleep 600ms
Type "echo   +-----------------------------------------------------------+"
Enter
Type "echo   : Step 1: read_file   src/pagination.py                    :"
Enter
Type "echo   : Step 2: exec        uv run pytest tests/test_paginate.py :"
Enter
Type "echo   : Step 3: apply_patch src/pagination.py (fix off-by-one)   :"
Enter
Type "echo   : Step 4: exec        uv run pytest tests/test_paginate.py :"
Enter
Type "echo   +-----------------------------------------------------------+"
Enter
Sleep 800ms

# -- Executor phase --
Type "echo [executor] Dispatching to Rust runner (sandbox: LinuxNamespace)"
Enter
Sleep 400ms
Type "echo   -- read_file src/pagination.py ... 200 OK (1.2 ms)"
Enter
Sleep 300ms
Type "echo   -- exec uv run pytest tests/test_paginate.py"
Enter
Sleep 600ms
Type "echo     FAILED tests/test_paginate.py::test_last_page - assert 11 == 10"
Enter
Type "echo     1 failed, 7 passed in 0.43s"
Enter
Sleep 500ms
Type "echo   -- apply_patch src/pagination.py"
Enter
Sleep 300ms
Type "echo     WARNING ApprovalRequired (428) -- mutation requires human approval"
Enter
Sleep 800ms

# -- Approval gate --
Type "echo [approval] Awaiting operator approval..."
Enter
Sleep 1200ms
Type "echo   OK Approved by operator@lula (HMAC token validated, nonce: a3f8c2)"
Enter
Sleep 500ms
Type "echo   -- apply_patch src/pagination.py ... 200 OK"
Enter
Sleep 300ms
Type "echo     [patch] --- src/pagination.py"
Enter
Type "echo     [patch] +++ src/pagination.py"
Enter
Type "echo     [patch] line 42:"
Enter
Type "echo     [patch]  -    end = min(offset + limit, total_items + 1)"
Enter
Type "echo     [patch]  +    end = min(offset + limit, total_items)"
Enter
Sleep 500ms
Type "echo   -- exec uv run pytest tests/test_paginate.py"
Enter
Sleep 600ms
Type "echo     8 passed in 0.39s"
Enter
Sleep 500ms

# -- Verifier phase --
Type "echo [verifier] Checking acceptance criteria"
Enter
Sleep 500ms
Type "echo   tests_pass: true  patch_size: 1 hunk  invariants: OK"
Enter
Type "echo   verdict: PASS"
Enter
Sleep 600ms

# -- Reporter phase --
Type "echo [reporter] Run complete"
Enter
Sleep 300ms
Type "echo   +------------------------------------+"
Enter
Type "echo   : run_id:   r-20260323-ab12cd34      :"
Enter
Type "echo   : status:   success                   :"
Enter
Type "echo   : loops:    1 / 3                     :"
Enter
Type "echo   : tools:    4 calls (1 mutation)      :"
Enter
Type "echo   : sandbox:  LinuxNamespace             :"
Enter
Type "echo   : duration: 3.7s                      :"
Enter
Type "echo   +------------------------------------+"
Enter
Sleep 1500ms

Set TypingSpeed 40ms

Type "echo."
Enter
Sleep 500ms
Type "echo # Done. Patch applied and verified."
Enter
Sleep 2000ms
