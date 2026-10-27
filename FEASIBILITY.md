# Job Application Assistant — Feasibility Assessment

Assessment date: 27 October 2026. This is a proposed design; no application or AWS resources have been created.

## Verdict

**Feasible as an AWS-hosted assistant that discovers opportunities, ranks them, prepares tailored documents, and tracks applications. Fully automatic applications across arbitrary job sites are not a dependable initial promise.** Integration access, custom questions, account challenges, and uncertain submission outcomes are the hardest parts.

Start with a useful approval-first product, then enable automatic submission individually for supported channels. Parallel processing is practical, provided application identity and submission coordination live in the database rather than in agent memory.

## Assumptions

- Initially one applicant, with a data model that can support more users later.
- Jobs, internships, and alternances; French and English documents.
- Existing Overleaf CV is the source template, supplemented by a verified profile of skills, education, and experience.
- Application hosting, document storage, processing, and LLM inference run on AWS. External job boards, mailbox providers, and Overleaf remain external dependencies.
- Target counts and spending limits are configurable. Finding a requested number of suitable opportunities cannot be guaranteed.

## Feature assessment

| Capability | Feasibility | Main qualification |
| --- | --- | --- |
| Search preferences and application dashboard | High | Conventional application development. |
| Cheap-model fit scoring | High | Evaluate against personal preferences; scores are rankings, not hiring probabilities. |
| Tailored CV and motivation letter | High | Ground every claim in verified facts and validate generated PDFs. |
| Application history and document versions | High | Store immutable submission snapshots and an event timeline. |
| Multiple job sources through adapters | High for accessible sources | Access, licensing, pagination, and rate limits differ. |
| Mailbox integration | Medium–high | OAuth, provider approval requirements, and ambiguous email matching. |
| Parallel preparation workers | High | Bounded concurrency, idempotency, and centralized quotas. |
| Duplicate prevention | High for recognized job identities | Cross-site copies without shared identifiers remain uncertain. |
| Universal unattended submission | Low as a general promise | Many sites lack applicant-accessible APIs or require interactive steps. |

## Integration findings

### Job boards: discovery and submission are separate capabilities

Greenhouse exposes job listings, but application POST requests require an API key. Lever also documents an employer-account API key for submission. A personal applicant should not assume access to these employer credentials. Both remain useful discovery integrations, with hosted application forms as a fallback. [Greenhouse documentation](https://developers.greenhouse.io/job-board.html), [Lever documentation](https://github.com/lever/postings-api).

LinkedIn's Apply Connect integration is part of its Talent Solutions partner ecosystem; it is not a general applicant automation API. Treat LinkedIn as a manual/import channel until an authorized integration is established. [LinkedIn partner information](https://business.linkedin.com/hire/ats-partners/partner-application).

France Travail currently reports that its offers API is no longer exposed through the normal portal while distribution and reuse are reviewed. Access requests go through its contact process. **Do not make initial delivery depend on access.** [France Travail API notice](https://www.data.gouv.fr/dataservices/api-offres-demploi).

For every source, verify allowed usage and available credentials before implementation. Browser automation is a later, site-specific capability requiring maintenance. Login challenges or CAPTCHA should hand control to the user.

### Overleaf: preserve the template, generate independent copies

The documented Overleaf API provides an “Open in Overleaf” import mechanism; it does not establish a general project-editing and compilation API for this design. Git integration supports pulling and pushing project files and is a premium feature. [Overleaf API](https://www.overleaf.com/devs), [Git integration](https://docs.overleaf.com/integrations-and-add-ons/git-integration-and-github-synchronization/git-integration).

Recommended approach: export the existing project, version the template on AWS, and compile tailored copies in isolated containers. Keep Overleaf as the human editing environment. Add explicit Git synchronization later if the account supports it; concurrent workers must never overwrite the master project.

### Email: integrate one provider first

Use OAuth and separate read permission from send permission. Gmail read access uses restricted scopes; distributing a server-hosted product can introduce verification and security assessment requirements. Personal-use exceptions need to be checked against the actual deployment. Testing-mode refresh tokens can also expire after seven days. [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes), [OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies), [token expiration](https://developers.google.com/identity/protocols/oauth2#expiration).

Start with scheduled incremental mailbox synchronization from AWS. Gmail push notifications require Google Cloud Pub/Sub and watch renewal, so they introduce an additional cloud dependency. [Gmail push documentation](https://developers.google.com/workspace/gmail/api/guides/push).

Email classification can suggest rejection, interview, or confirmation events. Ambiguous messages require review; an unrelated email must not silently change application status.

## Bedrock strategy

Use deterministic eligibility filters before any model call. Benchmark an inexpensive model, such as an available Nova Micro or Haiku model, for ranking and extraction. Benchmark a stronger Sonnet-class model for CV tailoring and letters. These are candidates, not fixed version selections: confirm availability, price, and French/English performance before choosing. [Bedrock model catalog](https://docs.aws.amazon.com/bedrock/latest/userguide/model-cards.html).

Keep model identifiers configurable. Set explicit output-token limits, cap retries, and record model, prompt version, input version, token usage, and validation results. A more expensive model does not guarantee factual accuracy. Unknown qualifications, work authorization, or alternance eligibility must remain unknown until supplied by the applicant.

## Duplicate prevention and reliability limits

Two protections are required:

1. **Identity resolution:** map source postings to one canonical employer opportunity using employer requisition IDs, ATS IDs, and normalized destination URLs. Similar titles and descriptions suggest duplicates but do not prove them.
2. **Submission coordination:** enforce a unique application per applicant and canonical opportunity, with atomic ownership checks before sending.

These controls stop competing workers from applying twice to a recognized opportunity. They cannot guarantee detection of every disguised repost. Unresolved likely duplicates should block automatic submission pending review.

External submission also cannot be made universally exactly-once: the destination may accept a request before the worker loses its connection. Record `submission_unknown`, retain evidence, and reconcile before another send. Queue deduplication alone does not solve this.

## Cost feasibility

The serverless design can suit intermittent personal use, but a monthly estimate needs measured token counts, selected models, worker duration, and source fees. Avoid presenting a fixed bill before those inputs exist.

For an initial measurement scenario, assume 1,000 distinct listings ranked and 100 document packages generated per month:

```text
LLM cost = sum((input_tokens × input_rate + output_tokens × output_rate) / 1,000,000)
total = LLM + API/compute + database + storage + workflow/queues
        + logs/secrets/network + external subscriptions
```

Rates above are per million tokens. Include retries, rejected drafts, compilation, and browser sessions. Use current [Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) and the AWS calculator after a small real workload benchmark. Overleaf subscriptions and paid source access are separate costs.

Use an application-enforced daily budget, atomic reservations before expensive work, bounded worker counts, and a kill switch. Billing alerts are useful notifications, not a hard spending stop.

## Main risks and mitigations

- **Fabricated CV claims:** generate from approved facts with provenance; reject unsupported content.
- **Malformed or unsafe LaTeX:** preserve a controlled template, escape generated text, disable shell execution, and compile without credentials or network access.
- **Prompt injection in listings/email:** treat external content as data; models cannot authorize submissions or acquire credentials.
- **Privacy exposure:** minimize mailbox ingestion, encrypt documents and tokens, restrict access, and define deletion and retention behavior.
- **Scaling too soon:** parallelize document preparation first; increase submission concurrency only after duplicate and timeout tests pass.

## Recommended decision

Proceed with an approval-first MVP: imported postings plus one accessible listing adapter, profile preferences, Bedrock ranking, tailored PDF generation, and a complete application timeline. Prove document quality and safe coordination before adding mailbox access and selective automatic submission. See [INITIAL_PLAN.md](INITIAL_PLAN.md) for the build sequence.
