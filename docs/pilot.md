# Pilot plan

Goal: prove `repo-index-mcp` saves engineers time in real agent-assisted work.

## Pilot cohort

Recruit 5 engineers who regularly work across unfamiliar or multi-repo code.

- DRI: repo maintainer.
- Support channel: project Slack/DM thread for setup issues and missed-query capture.
- Supported clients for pilot: mewrite/roktcode-style MCP JSON config, plus any client where the engineer can verify MCP tools are visible.
- Activation evidence location: this pilot table plus copied MCP `list_repos` / `search_code` success notes.

## Success metrics

- 5 engineers configure MCP within 4 weeks.
- 5 engineers activate: smoke test done, at least one work repo indexed, at least one successful MCP tool call.
- Each activated engineer uses MCP on at least 2 real tasks.
- 70% keep it enabled after week 2.
- At least 10 representative tasks measured with decision-grade timing.
- Primary metric: median observed context-assembly minutes reduced by 50% on decision-grade rows.
- Secondary metric: median manual file-pastes reduced by 50% on decision-grade rows.
- Recurring misses become new eval cases.

## Activation checklist

A pilot engineer is activated only when all are true:

1. `repo-index doctor` passes.
2. At least one work repo is indexed.
3. MCP client shows `search_code`, `get_symbol`, `list_repos`, `reindex`.
4. Engineer successfully calls `list_repos` through MCP.
5. Engineer successfully calls `search_code` through MCP and gets a relevant result.

## Task measurement template

| Task ID | Engineer | Date | Repo(s) | Task class | Baseline source | Baseline minutes | Tool minutes | Baseline files pasted | Tool files pasted | MCP queries | Misses | Useful? | Decision-grade? | Notes |
| --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | --- | --- | --- | --- | --- |
| pilot-001 |  |  |  |  | observed paired task / prior comparable / estimate |  |  |  |  |  |  |  | yes/no |  |

## Timing protocol

Decision-grade rows require observed baseline/tool timings.

- Start timer when engineer/agent begins looking for code context.
- Stop timer when the agent has enough concrete file/symbol context to act.
- Count manual files pasted into the agent.
- Count MCP queries used.
- Record whether returned snippets changed the agent's next action.

Baseline options:

- Best: same task class measured without `repo-index` before the pilot.
- Good: paired comparable task in same repo area.
- Weak: estimate only. Estimate-only rows are qualitative and do not count toward the 50% reduction metric.

## Weekly review

- Count activated users.
- Count active users after week 2.
- Review decision-grade task rows.
- Review missed queries.
- Add 5-10 new golden cases from real misses.

## Pilot decision gate

Expand beyond pilot only if all are true:

- At least 4 of 5 engineers activate.
- At least 70% keep MCP enabled after week 2.
- At least 10 decision-grade observed task rows are collected.
- Primary metric passes: median observed context-assembly minutes drops by at least 50%.
- No severe setup, security, or trust issue remains open.

If activation fails, fix onboarding/DX. If activation passes but primary metric fails, improve retrieval quality using pilot misses. If severe trust issues appear, pause sharing and fix safety/docs first.
