<instructions>
Review the vision for phase5, consider it in the context of the system we have built (the api calls for data ingestions, backtesting, model building)
Create a plan to help expand the platform using boto3, aws, langgraph to allow for a chat interface that can then accomplish this vision, from the ui. Document this plan by modifying @02_platform_plan.md to reflect it. Use your subagents to manage your context window, don't do it all yourself.
</instructions>

<vision>
Phase 5 is an autonomous forex research lab that can invent strategies, discover new features, run AutoML signal search, test ideas through disciplined backtesting, learn from failures, and iteratively evolve toward robust signals and strategies rather than one-off lucky backtests. It should have memory and ml and feature signals should help it gorw and learn.

First, you need a strategy research loop. That loop generates hypotheses, turns them into strategy definitions, backtests them, diagnoses failures, mutates them, and ranks survivors.

Second, you need a signal discovery loop. That loop generates new features, runs AutoML or other model-search jobs to see whether those features contain predictive information, turns winning model outputs into usable signals, and feeds those signals back into the strategy research loop.

Those two loops should share a common experiment memory, evaluation standard, and promotion policy. If they do not, the whole thing will drift into backtest overfitting.

The practical architecture is this.

At the top sits a research manager. Its job is to choose what to work on next: strategy mutation, feature discovery, model retraining, cross-validation, robustness review, or promotion. Beneath it are two major pipelines.

The first pipeline is the strategy pipeline. It starts with a hypothesis generator, then a strategy builder, then a backtest runner, then a diagnostics analyst, then a mutation planner. The output is not “final strategy found.” The output is an experiment record with evidence, comparison to prior runs, and a ranked recommendation for the next experiment.

The second pipeline is the signal pipeline. It starts with a feature ideation step, then feature materialization, then signal validation, then model search. For the model-search portion, SageMaker Autopilot can create AutoML jobs programmatically with CreateAutoMLJobV2, and it supports tabular regression/classification as well as time-series forecasting workflows, with generated notebooks and reports for candidate review. The signal pipeline’s output is not “deploy this model.” It is a vetted candidate signal with metadata, validation results, and a contract for how it can be consumed by strategies.

The glue between them is a research memory layer. That memory needs to track at least five things: experiment lineage, feature lineage, model lineage, validation context, and promotion status. Without that, the agent will keep rediscovering the same dead ends.

The way to organize the work is to define program phases.

Phase 5A should be the research operating system. Build the experiment registry, the evaluation schema, and the supervisor loop. This phase gives the agent a place to store every strategy attempt, every mutation, every feature set, every model artifact, and every backtest result. It also gives you a standard evaluation contract: return, drawdown, trade count, expectancy, regime sensitivity, cost sensitivity, out-of-sample performance, and parameter fragility.

Phase 5B should be constrained strategy search. In this phase, the agent is allowed to create strategies only from a bounded grammar and known signals. The goal is not open-ended creativity. The goal is to prove the experiment loop works. It should be able to create a baseline strategy, run it, explain why it failed, mutate one or two dimensions, rerun it, and compare the generations. This is the first place where the agent begins to behave like a research analyst rather than a workflow assistant.

Phase 5C should be feature discovery. Here the agent proposes new handcrafted features. These should come from interpretable families first: momentum, breakout structure, volatility compression/expansion, session features, distance-from-rolling-extremes, regime persistence proxies, and microstructure-aware cost proxies. The key is that feature generation must be constrained by metadata. Every feature should carry its lookback, dependency columns, transformation family, expected intuition, and leakage risk.

Phase 5D should be AutoML signal mining. In this phase, the agent packages labeled datasets and launches model-search jobs to see whether a new feature set has predictive value. SageMaker Autopilot is relevant here because it can run AutoML jobs for tabular prediction or time-series forecasting and produce performance and explainability artifacts you can inspect afterward. For your use case, I would start with tabular prediction for direction, return bucket, or forward risk-adjusted move, rather than time-series forecasting as the primary signal generator. That keeps the first signal contracts simpler.

Phase 5E should be signal-to-strategy integration. The output of the model-search phase should not be used raw. Each model candidate should be converted into one or more standardized signal contracts: probability of positive move, probability of breakout continuation, expected forward return bucket, regime class probability, or “do not trade” risk filter. Those signals become first-class citizens in the strategy DSL.

Phase 5F should be robustness and promotion. This is where you protect yourself from fooling yourself. Every candidate strategy or signal should go through holdout evaluation, walk-forward evaluation, cross-pair checks, cost stress, and parameter sensitivity review. Only after that should it be promoted to a “candidate strategy” or “candidate signal” tier.

The right agent team for this is also clearer now.

You need a research-manager agent that decides what experiment to run next. You need a strategy-researcher agent that writes or mutates strategies. You need a feature-researcher agent that proposes new features. You need a model-researcher agent that runs AutoML jobs and interprets the results. You need a backtest-diagnostics agent that explains failure modes and success conditions. You need a robustness-reviewer agent that tries to kill promising ideas before they graduate. And you need an experiment-librarian agent that tracks lineage and prevents duplicate work.

In terms of concrete system boundaries, the LLM should not do everything.

The LLM should choose the next experiment, write strategy hypotheses, describe candidate features, interpret results, and decide whether to mutate or discard.

Deterministic code should build feature matrices, launch jobs, poll jobs, parse artifacts, calculate metrics, compare generations, enforce leakage rules, enforce promotion gates, and maintain experiment lineage.

For AWS integration, Bedrock should be used for orchestration and reasoning through a clean adapter layer, and the Bedrock Converse API is the preferred consistent message interface for supported models. It requires bedrock:InvokeModel, and ConverseStream is available if you want streaming later. SageMaker Autopilot should sit behind a separate model-search service, not inside the strategy graph directly. That separation matters because model-search jobs are slower, heavier, and governed differently from strategy backtests.

The first MVP should be modest.

Do not start with “find a profitable strategy automatically.” Start with this:

The agent generates a constrained strategy from known features, backtests it, diagnoses the outcome, mutates one variable, reruns it, compares the two experiments, stores both in experiment memory, and recommends whether to continue exploring that branch.

Once that loop works, add one more slice:

The agent generates a small new feature family, materializes the dataset, launches a tabular AutoML job, inspects the candidate model metrics, converts the best model output into a standardized signal, and tests that signal inside one existing strategy family.

That is enough to prove the full direction without taking on the entire research lab at once.

A clean backlog would look like this.

First, build the research memory and experiment schema. Second, build constrained strategy generation and mutation. Third, build the backtest-and-diagnose loop. Fourth, build feature metadata and materialization. Fifth, build AutoML job submission and artifact parsing. Sixth, build signal contracts and strategy integration. Seventh, build promotion gates and robustness review.

The biggest risk is not technical integration. It is false discovery. If the agent gets too much freedom before you have strong experiment tracking and robustness gates, it will produce profitable-looking garbage. So the central design principle should be: expand creativity slowly, expand validation quickly.

The clean statement of the vision is this:

Phase 5 is an autonomous forex research lab that can invent strategies, discover new features, run AutoML signal search, test ideas through disciplined backtesting, learn from failures, and iteratively evolve toward robust signals and strategies rather than one-off lucky backtests.
</vision>