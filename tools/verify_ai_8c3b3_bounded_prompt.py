from app.ai.opportunity_scoring_service import OpportunityScoringService

print("Loaded scoring context budgets:")
print(OpportunityScoringService._SCORING_CONTEXT_BUDGETS)
print(
    "TOTAL =",
    sum(OpportunityScoringService._SCORING_CONTEXT_BUDGETS.values()),
)
