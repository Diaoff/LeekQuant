---
name: plan-audit
description: "Audit a project's codebase against a design/plan document. Read the plan, scan the code, and report implemented vs remaining with module-level completion percentages."
---

# Plan Audit

Audit a codebase against a design or planning document. Report what is implemented, what is partially done, and what is remaining, with module-level completion percentages.

## Trigger

User provides a plan or design document and asks to evaluate progress against it. Common phrasings:
- "评估下当前实现了哪部分?还剩余哪部分?"
- "根据文档为标准,评估当前功能开发以完成百分比"
- "How much of this plan is done?"
- "Audit this plan against the current codebase"

## Procedure

1. **Read the plan document** — Parse the provided doc (design doc, integration plan, PRD, roadmap) into discrete modules or milestones.

2. **Scan the codebase** — For each module/milestone in the plan, search the codebase for evidence of implementation. Look for:
   - Source files matching described components (routes, services, models, views, tests)
   - Database schemas, migrations, or models matching described data structures
   - API endpoints matching described interfaces
   - Frontend pages/components matching described features
   - Configuration, deployment, or infrastructure files matching described setup

3. **Classify each module** as one of:
   - ✅ **Implemented** — functional, tests present, matches plan spec
   - 🔶 **Partial** — skeleton or partial implementation exists, gaps remain
   - ❌ **Not started** — no code found matching this module

4. **Calculate completion percentages** — Per-module and overall. Weight by module size/complexity relative to the full plan.

5. **Output structured report** in this format:
   ```
   ## Overall: X% complete

   ## Module Breakdown
   | Module | Status | % | Evidence |
   |--------|--------|---|----------|
   | ...    | ✅/🔶/❌ | .. | key files/paths |

   ## Implemented (details)
   - [list key deliverables with file paths]

   ## Remaining (prioritized)
   1. [most critical gap]
   2. ...

   ## Risks / Notes
   - [any gaps, tech debt, or misalignments with the plan]
   ```

## Rules

- Cite specific file paths as evidence for every claim.
- Distinguish "code exists" from "code is functional" — check for tests, not just source files.
- If the plan has phases/milestones, report completion per phase.
- If no plan document is provided, ask the user to specify which document to audit against.
- Do not modify any files — this is a read-only audit.
