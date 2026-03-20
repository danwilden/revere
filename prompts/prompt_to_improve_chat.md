<task>
You are the orchestration lead for the Forex platform.

Your mandate is narrow and specific: close the remaining gaps in the recently implemented capability-realization slice without redesigning the architecture.

Assume the previous implementation is directionally correct and already landed:
- capability taxonomy
- capability inspector
- state-field distinction
- native exit primitives
- API exposure
- validation and tests

Your job is to wire, expose, harden, and complete that work.

You must act as an orchestrator, not a solo implementer.
Use multiple agent teams aggressively.
Decompose the work into narrow scopes, run parallel recon where file ownership does not collide, reconcile outputs, then integrate the smallest coherent patch set.

Do not do a broad rewrite.
Do not reopen settled architectural decisions unless you find a concrete defect that blocks the requested behavior.
Do not flatten everything into "features."
Do not build a general REPL.
Do not broaden scope beyond the gaps listed below.
</task>

<context>
The platform already has these architectural elements and they must be used, not reinvented:
See the @implementation.md for details
- LangGraph orchestration with supervisor + worker nodes
- chat/research agent surfaces
- Bedrock Converse tool calling
- strategy layer with StrategyState and event-driven on_bar logic
- feature discovery agent + feature sandbox + feature library
- signal materialization and wiring into the backtest DSL
- native strategy primitives already started
- capability inspection infrastructure already added

Preserve these boundaries:
- market/calendar-derived fields belong in the feature pipeline / feature runs
- state markers belong in strategy state / event-driven backtest engine
- native primitives remain native primitives
- capability inspection should classify and expose what exists and what is available in context
- legacy feature-run limitations should be explained cleanly, not hidden or papered over in prompts
</context>

<objectives>
Close exactly these gaps:

1. Agent tool registration
The capability inspector exists, but the actual Bedrock tool list and agent schema wiring used by strategy/chat/research flows still need to expose it so the LLM can proactively inspect capabilities.

2. days_in_trade support
days_in_trade is classified in the taxonomy/registry but is not yet actually injected/exposed by the engine the way bars_in_trade and minutes_in_trade are.

3. Feature-run/version awareness for calendar capabilities
The platform needs centralized, explicit handling for calendar-derived capability availability by feature-run version so legacy runs clearly report when a supported concept exists in the platform but is not available in the current run.

4. Cyclical calendar encodings
Calendar-derived features should include cyclical sine/cosine representations so periodic values have correct geometry.
Add cyclical encodings for:
- minute of hour
- hour of day
- day of week
- week of year
- month of year

Keep the raw interpretable fields too.
Do not replace raw calendar fields with sine/cosine only.
</objectives>

<design_principles>
Follow these principles strictly:

- Minimal patch set over broad refactor
- Additive and local changes over sweeping rewrites
- Backward compatibility where practical
- Centralize version-awareness rather than scattering ad hoc checks
- Derive lifecycle state from engine source of truth
- Compute calendar features centrally in the feature pipeline
- Expose both raw and cyclical calendar fields
- Keep prompt changes small and explicit
- Keep tests targeted and high-value
</design_principles>

<working_style>
You must work in this order:

1. Reconcile current repo state against the gaps above.
2. Launch multiple agent teams in parallel.
3. Require each team to return:
   - findings
   - exact file targets
   - recommended patch scope
   - risks / edge cases
4. Synthesize a short implementation memo.
5. Implement only the smallest coherent vertical patch set.
6. Run focused tests first, then short end-to-end proofs.
7. Report exactly what changed and what remains.

You are not allowed to skip the team-based recon step unless a team fails to run.
If a team fails, recover gracefully and continue with the other teams.
Do not let two teams modify the same core file at the same time.
</working_style>

<agent_teams>
Launch at least these teams.

<team name="agent-tooling-engineer">
Scope:
- inspect the actual Bedrock tool schemas and tool registration path used by the strategy/chat/research agent flows
- determine exactly where inspect_capability must be added
- determine whether any prompt/instruction changes are needed so the model uses the tool proactively rather than saying "unsupported"

Deliverable:
- exact files to edit
- exact schema/tool additions
- exact prompt deltas if needed
- misuse/overexposure risks
</team>

<team name="backtest-engineer">
Scope:
- add days_in_trade to the engine/state exposure path
- verify semantics and consistency with bars_in_trade and minutes_in_trade
- identify the cleanest source of truth for lifecycle timing

Deliverable:
- exact engine/state changes
- exact definition for days_in_trade
- edge cases for flat state, entry bar, and partial-day handling
</team>

<team name="capability-contract-engineer">
Scope:
- make capability inspection and/or validation version-aware for calendar-derived feature availability
- design the cleanest centralized way to express "supported by platform but unavailable in this legacy feature run"

Deliverable:
- exact contract/schema/API/inspection changes
- exact user-facing or agent-facing messages
- remediation guidance path
</team>

<team name="quant-feature-engineer">
Scope:
- inspect current calendar feature computation/materialization
- add cyclical encodings in the correct compute/materialization layer
- preserve raw calendar fields alongside cyclical encodings
- document conventions and leakage/sanity considerations
- identify feature-run version implications

Deliverable:
- exact fields to add
- exact formulas and period definitions
- exact indexing/normalization conventions
- exact file targets
</team>

<team name="test-verification-engineer">
Scope:
- propose the minimum high-value tests needed for all four gaps
- keep regression coverage tight and useful
- include at least one short end-to-end capability-inspection scenario

Deliverable:
- exact tests to add/update
- edge cases
- expected assertions
</team>

<team name="code-reviewer">
Scope:
- guard against scope creep and bad layering
- ensure days_in_trade is not shoved into the feature layer
- ensure cyclical encodings are not computed ad hoc in prompts
- ensure version-awareness is centralized
- identify modularity risks before final merge

Deliverable:
- critique of proposed patches
- simplification suggestions
- layering warnings
</team>
</agent_teams>

<implementation_requirements>
Implement the following behavior.

<gap_1_agent_tool_registration>
Add inspect_capability to the actual read-tool set used by agent nodes that draft, refine, diagnose, or research strategies.

Requirements:
- read-only tool
- exposed through the same path as other agent tools
- prompt or instruction updates should teach the model to inspect when a user asks for:
  - calendar/time-derived logic
  - holding-period logic
  - trade lifecycle markers
  - native exit primitives
- the model should stop dead-ending on "missing features" when the capability exists or can be classified
</gap_1_agent_tool_registration>

<gap_2_days_in_trade>
Implement days_in_trade in the engine/state exposure layer.

Requirements:
- source of truth must come from lifecycle timing/state, not a frontend workaround
- maintain consistency with bars_in_trade and minutes_in_trade
- clearly document semantics
- preferred default: fractional days based on elapsed minutes / 1440.0 unless the existing DSL or engine strongly requires another representation
- 0 when flat
- deterministic and testable
</gap_2_days_in_trade>

<gap_3_version_awareness>
Make calendar capability availability explicitly version-aware.

Requirements:
- distinguish between:
  - concept supported by platform taxonomy
  - concept available in this specific strategy context / feature run
  - concept unavailable because the feature run predates the upgraded calendar feature set
- provide clear remediation:
  - rerun the feature pipeline
  - use a compatible feature run
- do not produce vague failures
- centralize this logic rather than sprinkling checks
</gap_3_version_awareness>

<gap_4_cyclical_calendar_features>
Add cyclical calendar-derived features, alongside raw fields.

Required cyclical fields:
- minute_of_hour_sin
- minute_of_hour_cos
- hour_of_day_sin
- hour_of_day_cos
- day_of_week_sin
- day_of_week_cos
- week_of_year_sin
- week_of_year_cos
- month_of_year_sin
- month_of_year_cos

Retain raw interpretable fields where useful, including examples like:
- minute_of_hour
- hour_of_day
- day_of_week
- week_of_year
- month_of_year
- is_friday

Formula requirements:
- use standard cyclical encoding:
  sin(2π * value / period)
  cos(2π * value / period)

Document and implement conventions clearly:
- minute period = 60
- hour period = 24
- day of week period = 7
- week of year period must use a documented convention
- month period = 12
- clearly document whether:
  - day_of_week is 0-6 or 1-7
  - week_of_year follows ISO semantics
  - month is 1-12 and how it is normalized for the cycle

Behavior requirements:
- compute centrally in the feature pipeline
- expose through capability inspection
- include version-awareness messaging for legacy runs lacking the upgraded calendar feature set
- keep implementation deterministic
</gap_4_cyclical_calendar_features>
</implementation_requirements>

<testing_requirements>
Add or update targeted tests for all four gaps.

At minimum include:

1. Agent tool registration tests or equivalent proof
- inspect_capability is visible through the actual agent tool wiring
- agent instructions/prompts reference it appropriately if prompt tests exist

2. days_in_trade tests
- flat state => 0
- progresses consistently during an open position
- consistent with minutes_in_trade
- semantics documented and asserted

3. Version-awareness tests
- capability exists in taxonomy but unavailable in legacy feature run => explicit message and remediation
- capability available in newer run => clean pass

4. Cyclical calendar feature tests
- all sine/cosine outputs in [-1, 1]
- wraparound sanity for:
  - minute/hour boundary
  - day boundary
  - week boundary
  - month/year-cycle boundary as applicable
- registry / capability inspection exposure
- version-aware behavior for legacy runs

5. One short end-to-end scenario
Example shape:
- agent inspects a request like "exit after 2 days or avoid Friday close"
- capability inspection returns:
  - days_in_trade => supported state marker
  - exit_before_weekend => native primitive
  - day_of_week / cyclical calendar fields => supported calendar-derived fields subject to feature-run availability
- strategy can then be drafted without dead-ending
</testing_requirements>

<non_goals>
Do not:
- build a general-purpose REPL
- redesign the agent graph
- replace existing working primitive behavior unnecessarily
- perform a large feature-pipeline overhaul
- refactor unrelated modules for style only
- flatten strategy state into static feature storage
- implement calendar logic inside prompts instead of in code
</non_goals>

<before_coding_output>
Before making changes, produce a short memo with these sections:

1. Confirmed gaps
2. Team assignments
3. Proposed file changes
4. Proposed test additions
5. Risks and guardrails

Keep it concise and concrete.
</before_coding_output>

<execution>
After the memo:
- run the teams
- reconcile results
- implement the agreed patch set
- run targeted tests
- run a short end-to-end proof
</execution>

<final_output>
At the end, report:

1. Files changed
2. Tests added/updated
3. Exact behavior change for each of the 4 gaps
4. One example agent flow showing inspect_capability being used
5. One example strategy using days_in_trade
6. One example capability-inspection result showing legacy feature-run remediation
7. One example of the new cyclical calendar fields exposed by the system
8. Any remaining gaps, if any

Be disciplined.
This is a gap-closing pass, not a new phase.
</final_output>