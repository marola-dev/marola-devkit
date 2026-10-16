# Specification Quality Checklist: Org knowledge index

**Purpose**: Validate the spec before planning
**Created**: 2026-10-02
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] Focused on user value: maintainers and agents asking one question across eight repos
- [x] Every mandatory section completed
- [ ] No implementation details. Deliberately failed: the request names sqlite-vec, bge-m3, GCS
      and Besom, so FR-001, FR-008 and FR-016 name them too

## Requirement Completeness

- [x] No `[NEEDS CLARIFICATION]` markers remain
- [x] Requirements are testable: each FR maps to a self-test, a contract check or `eval`
- [x] Success criteria are measurable; SC-001 and SC-004 are already measured, SC-003 is a target
      that T014 measures
- [x] Edge cases cover every duplication source measured in `research.md` R2
- [x] Scope bounded: default branches only, no git history, no public bot (marola-dev/marola#398)
- [x] Dependencies and assumptions identified: Ollama, `gh`, the Phase 2 gate, public repos only

## Feature Readiness

- [x] Each user story is independently testable
- [x] P1 stories (US1 + US2) form a free MVP
- [x] The Phase 2 part (US4) is isolated and gated

## Notes

- Open after T014: the real bge-m3 CPU throughput, and whether CI can do a first full build or a
  laptop must seed it.
- Open with marola-dev/marola#398: where `infra/` lives.
