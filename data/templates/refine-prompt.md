# Spec Refinement Protocol

Given a draft spec from `landscape spec extract` or `landscape spec init`, refine it by:

## Steps

1. **Read the draft spec** — understand what was auto-detected or templated
2. **Read project docs** — README.md, CLAUDE.md, .claude/rules/, plans/, component registries
3. **For each component**:
   a. Are the `require` constraints tight enough? Add missing hard constraints based on project needs.
   b. What `prefer` fields matter? Assign weights 1-5 based on project priorities:
      - 5 = effectively required (massive penalty if unmet)
      - 4 = important
      - 3 = moderate (default)
      - 2 = nice to have
      - 1 = barely matters
   c. What requirements can't be auto-checked? Add to `notes` with specific details.
   d. When should this component be re-evaluated? Add `triggers` (concrete events, not vague).
4. **Check `stack_pins`** — are there committed tools that should constrain recommendations?
5. **Check `environment`** — does it accurately reflect the deployment target?
6. **Validate** with `landscape spec validate <spec.yaml>` before finalizing.

## Guidelines

- **Be specific in notes**: "needs SLURM sbatch integration" beats "HPC compatible"
- **Weight honestly**: don't make everything weight 5. Differentiate what truly matters.
- **Triggers should be events**: "Team exceeds 10 concurrent users" not "when needed"
- **Negation is powerful**: use `!cloud_only` when you know what you DON'T want
- **Stack pins reduce churn**: only pin tools you're committed to for 12+ months
- **Environment auto-injects**: `internet_on_compute: false` adds `offline_capable: true` everywhere

## Example Refinement Diff

```yaml
# BEFORE (auto-detected)
experiment_tracking:
  current_tool: MLflow
  require:
    categories: [experiment_tracking]

# AFTER (refined)
experiment_tracking:
  description: "Track hyperparameters, metrics, artifacts for IDS experiments"
  current_tool: MLflow
  require:
    offline_capable: true
    hpc_compatible: [native, adaptable]
    categories: [experiment_tracking]
    maturity: [growth, production]
  prefer:
    capability_ceiling: {value: extensive, weight: 5}
    community_momentum: {value: growing, weight: 3}
    python_native: {value: true, weight: 4}
    lock_in_risk: {value: low, weight: 2}
  notes:
    - "query_api for programmatic access to run data"
    - "artifact_storage for model checkpoints (10GB+)"
    - "compare runs across hyperparameter sweeps"
  triggers:
    - "Databricks restricts OSS MLflow features"
    - "Team exceeds 5 concurrent experiment writers"
```
