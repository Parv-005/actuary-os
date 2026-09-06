# Vortex ActuaryOS — Multi-Agent System Specification

## 1. Executive Summary

### 1.1 What Vortex ActuaryOS is

Vortex ActuaryOS is a multi-agent AI workforce designed to execute the repetitive operational layers surrounding actuarial work and present the actuary with a validated, explainable, review-ready decision package.

The core principle is:

> **AI prepares. Actuary decides.**

The system is not intended to replace the professional judgment of an actuary. Instead, it automates and coordinates the recurring workflow that can consume significant time before the actuary reaches the judgment-heavy part of the job.

The transformation is:

```text
TRADITIONAL WORKFLOW

Raw Data
   ↓
Collect
   ↓
Clean
   ↓
Validate
   ↓
Calculate
   ↓
Analyse
   ↓
Report
   ↓
Actuary
   ↓
Decision
```

becomes:

```text
VORTEX ACTUARYOS

Raw Data
   ↓
┌─────────────────────────────────────────┐
│          AI WORKFORCE / AGENTS          │
│                                         │
│  Intake → Data → Validation → Analysis  │
│              → Insight → Reporting      │
└─────────────────────────────────────────┘
   ↓
Human Actuary Review
   ↓
Professional Judgment
   ↓
Decision / Approval
```

The important product idea is therefore not:

> "An AI that calculates actuarial numbers."

It is:

> "An AI workforce that executes recurring actuarial workflows and converts raw operational data into decision-ready intelligence."

---

# 2. Problem We Are Solving

A recurring actuarial workflow can involve:

1. Receiving data from several systems/files.
2. Checking whether the expected data arrived.
3. Understanding and mapping fields.
4. Cleaning and standardizing data.
5. Detecting missing, duplicated, malformed or inconsistent records.
6. Reconciling totals with source systems and prior periods.
7. Running predefined calculations/models.
8. Comparing actual versus expected results.
9. Finding material changes and anomalies.
10. Investigating the drivers of those changes.
11. Checking historical context.
12. Looking up relevant assumptions/methodology.
13. Preparing charts, schedules and reports.
14. Writing an executive summary.
15. Recording exceptions and unresolved issues.
16. Giving the actuary enough evidence to make a professional decision.
17. Maintaining an auditable record of what happened.

The problem is not that every one of these activities is inherently difficult.

The problem is that they occur repeatedly, involve multiple handoffs, and sit around the high-value actuarial judgment.

Vortex reduces this operational burden while preserving human control over decisions.

---

# 3. Product Philosophy

## 3.1 Three principles

### Principle 1 — Automate execution, not accountability

The AI can execute a workflow.

The actuary remains accountable for professional judgment and final decisions.

### Principle 2 — Never hide uncertainty

When the system does not know something, it must explicitly say:

- what it knows,
- what it inferred,
- what it could not verify,
- what data is missing,
- and what requires human review.

### Principle 3 — Every important conclusion must be traceable

For material findings, the system should be able to answer:

> "Why did you say this?"

The answer should lead back through:

```text
Finding
  ↓
Analysis
  ↓
Calculation
  ↓
Underlying records / aggregates
  ↓
Source file/system
```

---

# 4. High-Level Multi-Agent Architecture

```text
                         ┌────────────────────┐
                         │   HUMAN ACTUARY    │
                         │ Review / Judgment   │
                         │ Approve / Override  │
                         └─────────┬──────────┘
                                   │
                                   │
                         ┌─────────▼──────────┐
                         │   ORCHESTRATOR     │
                         │ Workflow Manager   │
                         └─────────┬──────────┘
                                   │
             ┌─────────────────────┼─────────────────────┐
             │                     │                     │
             ▼                     ▼                     ▼
      ┌─────────────┐       ┌─────────────┐      ┌──────────────┐
      │ Intake Agent│       │  Data Agent  │      │ Validation   │
      │              │       │              │      │    Agent     │
      └─────────────┘       └─────────────┘      └──────────────┘
             │                     │                     │
             └─────────────────────┼─────────────────────┘
                                   ▼
                           ┌──────────────┐
                           │ Analysis     │
                           │ Agent        │
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │ Insight /    │
                           │ Investigation│
                           │ Agent        │
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │ Knowledge /  │
                           │ Research     │
                           │ Agent        │
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │ Reporting    │
                           │ Agent        │
                           └──────┬───────┘
                                  ▼
                           ┌──────────────┐
                           │ Audit / QA   │
                           │ Agent        │
                           └──────┬───────┘
                                  ▼
                           HUMAN ACTUARY
```

The architecture should be understood as a **coordinated system**, not a collection of independent chatbots.

---

# 5. Agent Responsibilities

## 5.1 Orchestrator Agent

### Purpose

The Orchestrator is the workflow manager.

It decides:

- which workflow should run,
- which agent should run next,
- what dependencies must be satisfied,
- whether the workflow can continue,
- when human approval is required,
- when a failed step should be retried,
- and when the workflow must stop.

### Example

A monthly portfolio review starts.

The Orchestrator creates:

```text
Workflow ID: APR-2026-09-001

Status: RUNNING

Required stages:
1. Intake
2. Data preparation
3. Validation
4. Analysis
5. Investigation
6. Reporting
7. QA
8. Human review
```

It then invokes the relevant agents.

### Orchestrator edge cases

#### Edge case A — Agent fails

Example:

```text
Validation Agent unavailable
```

Resolution:

- retry automatically within a safe limit,
- record the failure,
- do not silently skip validation,
- escalate to human if the dependency remains unavailable.

#### Edge case B — Agent produces incomplete output

Example:

```text
Analysis Agent completed only 8 of 10 required metrics.
```

Resolution:

- mark the stage incomplete,
- identify missing metrics,
- retry or rerun only the missing work,
- block final reporting until required outputs exist.

#### Edge case C — Circular workflow

An agent requests a task that causes the workflow to return to the same state repeatedly.

Resolution:

- detect repeated states,
- stop execution,
- surface the loop to the system administrator.

---

# 6. Intake Agent

## Purpose

The Intake Agent handles the arrival of new work.

It determines:

- what files/data arrived,
- which workflow they belong to,
- which reporting period they correspond to,
- whether expected inputs are present,
- and whether there are duplicate submissions.

## Example

Expected:

```text
claims_september.csv
premium_september.csv
policy_september.csv
```

Received:

```text
claims_september.csv
premium_september.csv
claims_september_v2.csv
```

The Intake Agent identifies that:

- claims appears twice,
- policy data is missing,
- one claims file may be a revised version.

It does **not** simply choose one silently.

It creates:

```text
INPUT EXCEPTION

Missing:
policy_september.csv

Potential duplicate:
claims_september.csv
claims_september_v2.csv

Human decision required:
Choose authoritative claims file or provide submission rule.
```

---

# 7. Data Agent

## Purpose

The Data Agent turns raw inputs into standardized analytical datasets.

It handles:

- file ingestion,
- column mapping,
- data type detection,
- schema normalization,
- joins,
- transformations,
- field standardization,
- data profiling.

## Example

Input:

```text
Policy ID
Pol_No
policy_number
POLICYNUM
```

The Data Agent can map these as equivalent fields when the workflow configuration and evidence support that mapping.

It produces a canonical internal schema:

```text
policy_id
claim_id
inception_date
expiry_date
premium
claim_amount
product
region
```

## Data Agent edge cases

### Missing column

Input lacks:

```text
claim_amount
```

Action:

- stop dependent calculations,
- identify affected metrics,
- report exactly what is blocked.

### Unexpected column

A new column appears:

```text
broker_segment
```

Action:

- retain it as an unmapped field,
- flag schema drift,
- do not use it automatically in calculations.

### Changed data type

Expected:

```text
premium = numeric
```

Received:

```text
premium = "₹1,20,000"
```

Action:

- attempt safe parsing,
- validate conversion,
- record transformation,
- reject ambiguous values.

### Duplicate rows

The system detects duplicate keys.

It should not automatically delete records unless there is a validated duplicate-removal rule.

Instead:

```text
Duplicate candidate count: 143

Status:
Needs rule-based resolution / human review
```

### Corrupted file

Action:

- quarantine the file,
- provide error details,
- do not continue as though the file were valid.

---

# 8. Validation Agent

## Purpose

The Validation Agent answers:

> "Can we trust this dataset enough to use it?"

Validation occurs at multiple levels.

### Level 1 — Structural validation

Check:

- columns,
- types,
- file format,
- row counts,
- mandatory fields.

### Level 2 — Record validation

Check:

- missing values,
- duplicates,
- invalid dates,
- negative values where prohibited,
- invalid categorical values,
- impossible relationships.

### Level 3 — Reconciliation

Compare:

- source totals,
- transformed totals,
- previous-period totals,
- system-of-record values.

### Level 4 — Behavioral validation

Look for:

- unusual shifts,
- sudden spikes,
- sudden drops,
- unexpected concentration.

## Example

Previous month:

```text
Policies = 500,000
Premium = 120M
Claims = 40M
```

Current month:

```text
Policies = 497,000
Premium = 119M
Claims = 95M
```

The Validation Agent should flag the claim movement.

But it should distinguish:

```text
Observation:
Claims increased substantially.

Possible explanations:
1. Actual deterioration
2. Large-loss event
3. Reporting delay/release
4. Duplicate data
5. Scope change
```

It should not declare one explanation as fact without evidence.

---

# 9. Validation Edge Cases

## 9.1 Missing values

Different fields require different policies.

For example:

```text
region missing
```

may be fixable through a reference table.

But:

```text
claim_amount missing
```

may block financial calculations.

Therefore validation should classify errors:

```text
INFO
WARNING
BLOCKER
```

## 9.2 Outliers

Outlier does not automatically mean bad data.

Example:

A claim of ₹50 crore may be unusual but valid.

The agent should say:

```text
Potential outlier detected.

Record:
Claim ID = C10291
Amount = ₹50 crore

Status:
Unusual but not proven invalid.
```

## 9.3 Historical inconsistency

If a product was renamed from:

```text
Commercial SME
```

to:

```text
Commercial Small Business
```

the agent should avoid concluding that the product disappeared.

A reference mapping or human confirmation is required.

## 9.4 Reconciliation mismatch

Source total:

```text
₹100M
```

Transformed total:

```text
₹98.7M
```

The workflow should stop at the affected stage until the discrepancy is explained or explicitly accepted by an authorized human.

---

# 10. Analysis Agent

## Purpose

The Analysis Agent executes predefined actuarial/portfolio analyses.

The key design rule:

> **Calculations should be deterministic and reproducible wherever possible.**

The LLM should not be used as a calculator when a verified computational tool can perform the calculation.

The Analysis Agent should call:

- SQL,
- Python/statistical functions,
- configured actuarial calculation modules,
- approved model implementations.

## Example

The workflow may calculate:

```text
Claim Frequency
Claim Severity
Loss Ratio
Actual vs Expected
Period-over-Period Change
Segment Contribution
```

For example:

```text
Loss Ratio = Claims / Premium
```

The calculation engine produces the number.

The AI then explains the result.

---

# 11. Analysis Agent Edge Cases

## Missing baseline

Actual vs expected requires an expected value.

If expected data is unavailable:

```text
Cannot calculate Actual vs Expected.

Reason:
Expected metric not available for current segment.

Action:
Human review required.
```

## Division by zero

If:

```text
Premium = 0
Claims > 0
```

do not produce infinity or a misleading percentage.

Return:

```text
Loss ratio:
Undefined because premium base is zero.
```

## Small sample size

A segment with only 2 policies may have a huge percentage movement.

The agent should flag:

```text
High movement + low exposure.

Interpret cautiously.
```

## Model unavailable

If the configured calculation model cannot execute:

- do not replace it with an unapproved model,
- mark the calculation unavailable,
- notify the human.

---

# 12. Insight / Investigation Agent

## Purpose

This is the agent that moves Vortex beyond basic automation.

It asks:

> "What is driving the observed movement?"

It can drill down through dimensions such as:

```text
Portfolio
   ↓
Product
   ↓
Region
   ↓
Customer / segment
   ↓
Claim type
   ↓
Time
```

## Example

Overall loss ratio:

```text
+7.8%
```

The Insight Agent investigates.

```text
Portfolio
  ↓
Commercial
  ↓
South Region
  ↓
Construction
  ↓
Higher claim severity
```

It might produce:

```text
Finding:

Commercial Construction in the South region
is the largest contributor to the portfolio deterioration.

Evidence:

- Segment loss ratio: +15.2%
- Claim severity: +13.1%
- Segment contributes 61% of total deterioration
- Movement concentrated in the latest period
```

The agent should distinguish:

### Evidence

What the data directly supports.

### Hypothesis

What might explain it.

### Conclusion

What can reasonably be concluded given the available evidence.

---

# 13. Insight Agent Edge Cases

## Correlation vs causation

If two things moved together:

```text
High claim severity
+
Severe weather period
```

the agent should not automatically say:

> "Weather caused the increase."

Instead:

> "The movement coincides with the severe weather period; causation has not been established."

## Multiple drivers

If:

```text
Driver A = 35%
Driver B = 32%
Driver C = 20%
Other = 13%
```

the agent should say there are multiple material contributors.

It should not force a single-cause narrative.

## No clear driver

Sometimes there is no statistically or operationally dominant driver.

Output:

```text
No single dominant driver identified.

Movement is distributed across multiple segments.
```

## Conflicting evidence

If one dataset indicates deterioration but another source indicates stability:

```text
Evidence conflict detected.

Do not finalize causal explanation.
Human review required.
```

---

# 14. Knowledge / Research Agent

## Purpose

This agent retrieves approved contextual information from the organization's knowledge base.

Possible sources:

- previous actuarial reports,
- methodology documents,
- assumptions,
- business definitions,
- internal policies,
- historical analyses,
- model documentation.

It should provide source references for important claims.

## Example

The Insight Agent detects:

```text
Reserve assumption changed last quarter.
```

The Knowledge Agent searches approved documentation and returns:

```text
Relevant documentation:
Reserve Methodology v3.1
Effective: Q2 2026

Relevant assumption:
...
```

It should not invent internal policy.

## Knowledge Agent edge cases

### No relevant document

Return:

```text
No supporting internal documentation found.
```

### Conflicting documents

Return:

```text
Potential documentation conflict.

Document A:
Version 3.0

Document B:
Version 3.1

Latest effective version could not be established automatically.
```

Then require human confirmation.

### Outdated document

Flag:

```text
Document exists but may be superseded.
```

---

# 15. Reporting Agent

## Purpose

The Reporting Agent converts validated analytical outputs into human-readable deliverables.

It can create:

- executive summaries,
- detailed reports,
- Excel schedules,
- charts,
- review dashboards,
- presentation-ready findings.

## Crucial rule

The Reporting Agent should not invent numbers.

Every numerical statement should originate from:

```text
Approved calculation output
```

It should maintain links to the underlying result.

## Example

Instead of dumping 30 tables on the actuary, it creates:

```text
MONTHLY PORTFOLIO REVIEW

Status: Ready for actuarial review

Portfolio:
Loss ratio +4.2%

Top movement:
Commercial +11.7%

Primary contributor:
Construction segment

Potential issue:
Higher claim severity in South region

Human review:
Assess whether the movement reflects
temporary large-loss volatility or emerging trend.
```

---

# 16. Audit / QA Agent

## Purpose

The QA Agent acts as the final consistency checker before the work is presented as ready for review.

It checks:

- required stages completed,
- calculations present,
- report numbers match calculation outputs,
- no blocked validation remains,
- material exceptions are visible,
- citations/traces exist,
- human approvals are recorded.

## Example

Analysis engine says:

```text
Loss Ratio = 11.7%
```

Report says:

```text
Loss Ratio = 11.1%
```

QA Agent blocks publication:

```text
REPORT CONSISTENCY FAILURE

Calculation output: 11.7%
Report output: 11.1%

Final report cannot be marked ready.
```

This is essential.

---

# 17. Human-in-the-Loop Architecture

Human-in-the-loop should not mean:

> "Ask the human about everything."

That destroys the productivity benefit.

Instead, use **risk-based human checkpoints**.

The system should autonomously continue through low-risk operations and stop only when:

- a decision has professional consequences,
- data is ambiguous,
- evidence conflicts,
- an important assumption changes,
- the system cannot establish sufficient confidence,
- or an action requires authorization.

---

# 18. Human Actuary: What They See at Each Step

## Stage 0 — Before the workflow

### Human sees

```text
MONTHLY REVIEW

Reporting period:
September 2026

Expected inputs:
✓ Claims
✓ Premium
✓ Policy exposure
✓ Reference mapping

Workflow:
Ready to start
```

### Human input required?

Normally:

**No.**

The workflow can start automatically.

The actuary may configure:

- reporting period,
- portfolio,
- thresholds,
- methodology/model version.

---

# 19. Stage 1 — Intake

### Human sees

```text
INPUT STATUS

✓ Claims data received
✓ Premium data received
✓ Policy data received

3 / 3 required inputs

No duplicate submissions detected.

[Continue Automatically]
```

### Human input required?

Usually:

**No.**

Human intervention only if there is ambiguity.

Example:

```text
Two versions of claims data found.

claims_v1.csv
claims_v2.csv

Which is authoritative?

[Select v1]
[Select v2]
[Provide rule]
```

---

# 20. Stage 2 — Data Preparation

### Human sees

```text
DATA PREPARATION

Rows processed: 4,823,201

Schema:
✓ Valid

Transformations:
12 standardizations
3 type conversions
0 blocked transformations

Unmapped columns:
2

[View Details]
```

### Human input required?

Usually:

**No.**

Human input occurs when mappings are ambiguous.

Example:

```text
Unmapped field:

"BizClass"

Possible mappings:

○ Product
○ Business Segment
○ Underwriting Class

[Confirm]
```

---

# 21. Stage 3 — Validation

This is the first major potential human checkpoint.

### Human sees

```text
VALIDATION STATUS

✓ Schema validation
✓ Required fields
✓ Reconciliation
✓ Duplicate check

⚠ 1 warning
🔴 1 blocker

BLOCKER:
Premium total differs from source system by 3.4%.

[Investigate]
```

The actuary can inspect:

- the discrepancy,
- source totals,
- transformed totals,
- affected records,
- likely causes.

### Human decisions

```text
[Reject Data]
[Fix Data]
[Accept Exception]
[Request Re-run]
```

The human is not expected to find the discrepancy manually.

The AI finds it.

The human decides whether the discrepancy is acceptable.

---

# 22. Stage 4 — Analysis

### Human sees

```text
ANALYSIS COMPLETE

Loss Ratio:
Current      67.3%
Previous     63.1%
Expected     62.8%

Movement:
+4.2 pts vs previous
+4.5 pts vs expected

[View Calculation]
[View Data]
```

### Human input required?

Usually:

**No**, provided the configured methodology and model were pre-approved.

The actuary may inspect results.

---

# 23. Stage 5 — Insight / Investigation

This is where human judgment becomes more important.

### Human sees

```text
TOP FINDINGS

1. Commercial portfolio deterioration
   Contribution: 64%

2. Construction segment severity increased
   +13.1%

3. South region concentration increased
   +8.7%
```

The system should allow the actuary to drill into the evidence.

### AI also shows:

```text
Confidence:
High

Evidence:
7 supporting analyses

Alternative explanations:
Large-loss volatility
Reporting timing
Emerging frequency trend
```

### Human input required?

Not necessarily.

But the actuary should be able to:

```text
[Accept Finding]
[Reject Finding]
[Request Deeper Analysis]
[Add Comment]
```

---

# 24. Stage 6 — Knowledge / Assumption Review

Suppose the AI identifies an assumption potentially related to the movement.

### Human sees

```text
ASSUMPTION ALERT

Current observed severity:
+13.1%

Configured assumption:
+5.0%

Potential variance:
+8.1 pts

Relevant methodology:
Reserve Methodology v3.1

AI interpretation:
Current experience is above the configured
assumption and may warrant actuarial review.

AI does NOT recommend an assumption change.
```

### Human input

This is a meaningful human checkpoint.

The actuary decides:

```text
[No Change Required]
[Investigate Further]
[Review Assumption]
[Escalate]
```

This protects the boundary between **AI analysis** and **professional actuarial judgment**.

---

# 25. Stage 7 — Reporting

### Human sees

A generated report preview.

```text
MONTHLY ACTUARIAL REVIEW

Executive Summary
Key Metrics
Key Drivers
Exceptions
Charts
Supporting Evidence
Open Questions

Status:
Draft — Awaiting Actuary Approval
```

### Human actions

```text
[Approve]
[Edit]
[Send Back for Revision]
[Add Commentary]
```

The AI should learn from explicit corrections within the workflow context, but an important professional change should not silently alter the underlying methodology.

---

# 26. Stage 8 — Final Approval

### Human sees

```text
FINAL REVIEW

✓ Data validated
✓ Calculations complete
✓ Findings traceable
✓ Exceptions reviewed
✓ Report QA passed

Open actuarial decisions:
2

1. Commercial construction deterioration
2. Assumption review

Final status:
Awaiting actuarial judgment
```

The actuary approves or records decisions.

Example:

```text
Decision:
Monitor for two additional periods.

Comment:
Current movement is material but insufficient
to justify assumption revision at this stage.
```

This becomes part of the audit trail.

---

# 27. Complete End-to-End Example

## Scenario

An insurer runs a monthly portfolio review.

### Input

```text
claims_august.csv
premium_august.csv
exposure_august.csv
```

---

## Step 1 — Intake

System identifies all expected files.

```text
Status: PASS
```

No human required.

---

## Step 2 — Data Agent

Standardizes:

```text
policy_no → policy_id
claim_amt → claim_amount
```

No human required.

---

## Step 3 — Validation

Detects:

```text
1.2% increase in claims
```

Nothing structurally wrong.

But it detects:

```text
Commercial claims:
+19%
```

This is a warning, not a blocker.

---

## Step 4 — Analysis

Calculation engine produces:

```text
Portfolio loss ratio:
63.1% → 67.3%

Change:
+4.2 percentage points
```

---

## Step 5 — Insight Agent

Drills down:

```text
Commercial
   ↓
Construction
   ↓
South region
```

Finds:

```text
Claim severity +13.1%
Claim frequency +2.4%
```

Conclusion:

```text
Deterioration appears more strongly associated
with severity than frequency.
```

---

## Step 6 — Knowledge Agent

Finds:

```text
Previous actuarial review:

"Monitor construction severity over the
next reporting periods."
```

The current result therefore gets additional priority.

---

## Step 7 — Reporting Agent

Creates:

```text
Executive Finding:

Commercial construction business is the
primary contributor to current portfolio
deterioration.

The movement is driven more by severity
than frequency.

This issue was also flagged for monitoring
in the prior review.
```

---

## Step 8 — QA Agent

Verifies:

```text
Every number matches the calculation engine.
Every finding has supporting evidence.
No blocked validation issues remain.
```

---

## Step 9 — Human Actuary

The actuary sees:

```text
ATTENTION REQUIRED

Why this matters:
Deterioration is material.

Evidence:
✓ +13.1% severity
✓ 61% contribution to portfolio deterioration
✓ Repeat monitoring item from prior period

AI assessment:
High-confidence observation

Human decision:
Does this require assumption review,
pricing review, or continued monitoring?
```

The actuary investigates and selects:

```text
Continue monitoring for one more period.
No assumption change currently required.
```

The system records the decision.

---

# 28. What the Human Should NOT Have to Do

The system should remove repetitive actions such as:

```text
Downloading files
Copying data between spreadsheets
Renaming columns
Manually checking every row
Running repetitive formulas
Comparing hundreds of segments manually
Building recurring charts
Copying numbers into reports
Searching old reports manually
Writing the first draft of the summary
Checking every report number manually
```

The human's time should instead move toward:

```text
Interpretation
Judgment
Challenge
Scenario thinking
Business implications
Assumption decisions
Communication
Final approval
```

---

# 29. Confidence Model

The system should not treat every output equally.

Each material AI finding should ideally have:

```text
Finding
Confidence
Evidence count
Data quality status
Potential alternative explanations
Human review requirement
```

Example:

```text
Finding:
Commercial construction is the main contributor.

Confidence:
High

Evidence:
6 independent supporting checks

Data quality:
Passed

Alternative explanation:
Large-loss volatility

Human review:
Required before any assumption change
```

---

# 30. Risk Tiers for Human Review

A useful prototype policy is:

## Green — Autonomous

Examples:

- formatting,
- column standardization,
- deterministic calculations,
- report generation,
- routine comparisons.

Human does not need to intervene.

## Yellow — Review recommended

Examples:

- unusual movement,
- outlier,
- ambiguous data mapping,
- weak evidence,
- conflicting historical context.

System can continue but prominently flags the issue.

## Red — Human approval required

Examples:

- unresolved data integrity issue,
- methodology ambiguity,
- assumption change,
- final actuarial conclusion,
- material override,
- external submission,
- professional recommendation.

The workflow pauses until an authorized human acts.

---

# 31. Human Override Mechanism

Every important AI output should support:

```text
ACCEPT
REJECT
EDIT
INVESTIGATE
OVERRIDE
```

If the actuary rejects an AI finding:

```text
AI:
"Commercial deterioration driven by severity."

Actuary:
"Reject"

Reason:
"Large-loss reserve release distorted the result."
```

The system records:

```text
Original AI finding
Human override
Reason
Timestamp
User
Affected report/version
```

This creates an auditable human-in-the-loop system.

---

# 32. Error Handling Philosophy

Never hide errors.

There are three kinds of outcomes:

### PASS

```text
Workflow can continue.
```

### WARNING

```text
Workflow can continue, but the issue is surfaced.
```

### BLOCKER

```text
Workflow cannot continue safely.
Human action required.
```

Example:

```text
WARNING:
Small sample size.

BLOCKER:
Premium reconciliation failed by 18%.
```

---

# 33. Important Edge Cases Across the Entire System

## Data arrives late

The workflow identifies missing inputs.

```text
Status:
Waiting for policy data.
```

No false "complete" result is generated.

## Data arrives twice

The Intake Agent detects duplicate submissions.

## Data schema changes

The Data Agent detects schema drift.

## Data has unexpected values

Validation flags invalid or suspicious values.

## Source systems disagree

The workflow records the conflict.

## A model fails

The system does not substitute an unapproved model.

## A calculation is undefined

The system explains why rather than producing a misleading number.

## An anomaly is real but rare

The system distinguishes unusual from invalid.

## No anomaly exists

The system must be able to say:

```text
No material deviation identified.
```

It should not invent findings merely because an AI summary is expected.

## Multiple explanations exist

The system presents multiple plausible drivers.

## Evidence conflicts

The system explicitly marks the finding as uncertain.

## AI cannot explain a conclusion

The conclusion should not be promoted as a material finding.

## AI is asked to make a professional judgment

The workflow routes the issue to the actuary.

## Human disagrees with AI

Human override is supported and recorded.

## Human changes a decision

The report and audit trail maintain version history.

## Agent times out

The orchestrator retries safely and records the attempt.

## Partial workflow completion

The system resumes from the last valid checkpoint rather than rerunning everything unnecessarily.

---

# 34. State Machine for the Workflow

A useful implementation model is:

```text
CREATED
   ↓
INPUT_WAIT
   ↓
INGESTING
   ↓
VALIDATING
   ↓
VALIDATED
   ↓
ANALYZING
   ↓
ANALYZED
   ↓
INVESTIGATING
   ↓
INSIGHTS_READY
   ↓
REPORTING
   ↓
QA
   ↓
HUMAN_REVIEW
   ↓
APPROVED
   ↓
COMPLETED
```

Possible alternate states:

```text
BLOCKED
WAITING_FOR_HUMAN
FAILED
RETRYING
REJECTED
```

This makes the multi-agent system much more reliable than simply chaining prompts.

---

# 35. What the Dashboard Should Show

The main dashboard should be designed around the actuary's decisions, not around the underlying AI implementation.

## Top bar

```text
Monthly Portfolio Review
September 2026

Status:
Awaiting Actuary Review
```

## KPI cards

```text
Loss Ratio     Actual vs Expected     Claim Severity
67.3%          +4.5 pts               +13.1%
```

## AI findings

```text
🔴 High Priority
Commercial construction deterioration

🟠 Review
South-region concentration

🟢 Stable
Health portfolio
```

## Data quality

```text
✓ Source validation
✓ Reconciliation
✓ Duplicate check

1 warning
0 blockers
```

## Human decisions

```text
2 decisions required

[Review]
```

---

# 36. Drill-Down Experience

The actuary should be able to click:

```text
"Commercial deterioration"
```

and see:

```text
WHY?

Contribution:
64%

Breakdown:

Construction        42%
Other Commercial    22%

Region:
South                31%
West                 18%
North                9%

Metric:
Severity             +13.1%
Frequency             +2.4%
```

Then:

```text
[View supporting data]
[View calculation]
[View previous period]
[View previous actuarial comment]
```

This creates a chain from summary → evidence.

---

# 37. Explainability Model

For every important AI finding, store:

```text
Finding ID
Workflow ID
Data sources
Calculation IDs
Agent that generated the finding
Agent version
Model/version if applicable
Evidence
Confidence
Human decisions
Timestamp
```

This gives Vortex a defensible audit trail.

---

# 38. Recommended Agent-to-Tool Design

The agents should not directly perform every low-level operation.

A good architecture is:

```text
AGENT
  ↓
DECIDES WHAT TO DO
  ↓
TOOL
  ↓
PERFORMS DETERMINISTIC OPERATION
  ↓
RESULT
  ↓
AGENT INTERPRETS RESULT
```

For example:

```text
Insight Agent
    ↓
"Compare severity across regions"
    ↓
Analytics Tool
    ↓
Returns grouped statistics
    ↓
Insight Agent interprets
```

This reduces hallucination risk.

---

# 39. What Makes the System "Multi-Agent"?

It is multi-agent because different agents have distinct responsibilities and can independently reason about their own stage of the workflow.

For example:

```text
Data Agent:
"Is the data structurally usable?"

Validation Agent:
"Can we trust the data?"

Analysis Agent:
"What do the configured calculations show?"

Insight Agent:
"What is driving the change?"

Knowledge Agent:
"What does our existing documentation say?"

Reporting Agent:
"How should this be communicated?"

QA Agent:
"Is the final output internally consistent?"
```

The Orchestrator coordinates them.

The Human Actuary owns the professional judgment.

---

# 40. What Not to Build

For the first Vortex prototype, avoid trying to build:

- a complete actuarial modeling platform,
- every type of insurance workflow,
- every regulatory process,
- fully autonomous actuarial decisions,
- unrestricted autonomous modification of assumptions,
- a generic chatbot with no workflow.

Instead, build one narrow workflow end-to-end.

Recommended wedge:

> **Recurring Monthly Portfolio Review**

This is enough to demonstrate:

```text
Data ingestion
      ↓
Validation
      ↓
Analysis
      ↓
Investigation
      ↓
Reporting
      ↓
Human review
```

---

# 41. MVP Agent Set

The first prototype can use:

```text
1. Orchestrator Agent
2. Intake/Data Agent
3. Validation Agent
4. Analysis Agent
5. Insight Agent
6. Reporting Agent
7. QA Agent
8. Human Actuary
```

The Knowledge Agent can be added once internal documents and historical context are part of the prototype.

---

# 42. MVP End-to-End Flow

```text
                 ┌──────────────────┐
                 │   Upload Files   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Intake/Data      │
                 │ Agent            │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Validation       │
                 │ Agent            │
                 └────────┬─────────┘
                          ↓
                    PASS / BLOCK
                          ↓
                 ┌──────────────────┐
                 │ Analysis Agent   │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Insight Agent    │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ Reporting Agent  │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ QA Agent         │
                 └────────┬─────────┘
                          ↓
                 ┌──────────────────┐
                 │ HUMAN ACTUARY    │
                 │ Review / Decide  │
                 └────────┬─────────┘
                          ↓
                       COMPLETE
```

---

# 43. The Most Important Human Checkpoints

The system should deliberately ask the actuary for input at these moments:

### Checkpoint 1 — Ambiguous input

Example:

```text
Which file is authoritative?
```

### Checkpoint 2 — Material unresolved data issue

Example:

```text
Source and transformed totals differ.
```

### Checkpoint 3 — Methodology/assumption issue

Example:

```text
Observed experience materially differs from assumption.
```

### Checkpoint 4 — Conflicting evidence

Example:

```text
Two data sources imply different explanations.
```

### Checkpoint 5 — Material professional conclusion

Example:

```text
Does this warrant assumption/pricing/reserve action?
```

### Checkpoint 6 — Final approval

The actuary approves the final output.

---

# 44. What the AI Owns vs What the Actuary Owns

| Activity | AI | Actuary |
|---|---:|---:|
| Collect data | Own | Oversight |
| Standardize data | Own | Review exceptions |
| Detect data issues | Own | Resolve material exceptions |
| Run configured calculations | Own | Review |
| Detect anomalies | Own | Interpret |
| Investigate drivers | Own | Challenge / validate |
| Find supporting documents | Own | Review relevance |
| Draft report | Own | Edit / approve |
| Suggest investigation areas | Own | Decide significance |
| Change assumptions | No | Own |
| Make final professional judgment | No | Own |
| Approve final report | No | Own |
| Override AI finding | Support | Own |
| Record professional rationale | Assist | Own |

---

# 45. The Core Value Proposition

The strongest way to describe Vortex is:

> **Vortex compresses a multi-step recurring actuarial workflow into an AI-prepared review, so the actuary spends less time operating the process and more time exercising professional judgment.**

Or visually:

```text
BEFORE

Data
 ↓
Collect
 ↓
Clean
 ↓
Validate
 ↓
Calculate
 ↓
Analyse
 ↓
Report
 ↓
ACTUARY
 ↓
Decision


AFTER

Data
 ↓
┌──────────────────────────────────┐
│          VORTEX AI WORKFORCE     │
│                                  │
│ Collect → Clean → Validate       │
│ → Calculate → Investigate        │
│ → Explain → Report               │
└──────────────────────────────────┘
 ↓
ACTUARY
 ↓
Judgment
 ↓
Decision
```

---

# 46. The Fundamental Product Boundary

Vortex should repeatedly enforce this boundary:

```text
AI

"What happened?"
"What changed?"
"Where did it change?"
"What appears to be driving it?"
"What evidence supports it?"
"What deserves your attention?"
```

versus:

```text
ACTUARY

"Is this material?"
"Is the explanation credible?"
"Does this change my view?"
"Should an assumption change?"
"What action should the business take?"
"Can I approve this conclusion?"
```

This boundary is the foundation of the human-in-the-loop design.

---

# 47. Final Product Mental Model

Do not think of Vortex as:

```text
CHATBOT + TOOLS
```

Think of it as:

```text
                  VORTEX ACTUARYOS
                         │
              ┌──────────▼──────────┐
              │ Workflow Orchestrator│
              └──────────┬──────────┘
                         │
       ┌─────────────────┼──────────────────┐
       ▼                 ▼                  ▼
     DATA             VALIDATE            ANALYZE
       │                 │                  │
       └─────────────────┼──────────────────┘
                         ▼
                     INVESTIGATE
                         │
                         ▼
                       EXPLAIN
                         │
                         ▼
                       REPORT
                         │
                         ▼
                        QA
                         │
                         ▼
                ┌─────────────────┐
                │  HUMAN ACTUARY  │
                │                 │
                │ Review          │
                │ Challenge       │
                │ Decide          │
                │ Approve         │
                └─────────────────┘
```

The AI workforce does the operational heavy lifting.

The human provides the professional judgment.

---

# 48. One-Sentence Definition for the Vortex Case

> **Vortex ActuaryOS is a human-in-the-loop multi-agent AI workforce that automates the recurring data, validation, analysis, investigation, and reporting workflow surrounding actuarial judgment—turning raw data into an explainable, auditable, decision-ready review for the actuary.**

---

# 49. Prototype Success Criteria

A successful prototype should demonstrate that:

1. A user can provide raw recurring data.
2. The system automatically recognizes the workflow.
3. Data is prepared without repeated manual manipulation.
4. Validation catches realistic problems.
5. Deterministic calculations are executed correctly.
6. The system identifies material movements.
7. The system investigates likely drivers.
8. Findings include supporting evidence.
9. The final report is generated automatically.
10. QA catches inconsistencies before release.
11. The actuary can inspect the evidence behind any material finding.
12. The actuary can accept, reject, override, or annotate findings.
13. Professional decisions remain explicitly human.
14. Every major action is recorded in an audit trail.
15. The system fails safely when confidence or data quality is insufficient.

The strongest demonstration is therefore not:

> "Look, the AI generated a report."

It is:

> **"Watch the entire recurring workflow execute automatically, watch the system detect a real exception, watch it investigate the exception, and then watch the actuary make the final decision using an evidence-backed review package."**
