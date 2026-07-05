---
title: Generic deploy rollback procedure
slug: deploy-rollback
keywords: deploy rollback release revert bad deploy regression canary
alertnames:
---

# Generic deploy rollback

Use this when an incident correlates with a recent deploy and no specific runbook applies.

1. Identify the suspect release: compare incident start time with the deploy log
   (`git log` on the service repo; look for changes to the affected code path).
2. Roll back: redeploy the previous known-good tag, or disable the feature flag that
   gated the change.
3. Verify metrics return to baseline before closing the incident.
4. File a postmortem and link the offending commit.
