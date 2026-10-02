# Contract: GCS hosting declared in Besom (User Story 4, Phase 2)

**Gate**: GCP is Phase 2 (`docs/PHASES.md`), blocked by marola-dev/marola#447. `pulumi preview` is
free and may run in CI. `pulumi up`, `destroy` and `import` run only by a human after an explicit
go-ahead with the cost below. Nothing here has been provisioned.

## Shape

One private, versioned object per bucket. A publish overwrites it; versioning keeps the previous 5
generations for 30 days, so rollback is a copy of an older generation, not a pointer flip.

```text
gs://<bucket>/org-index.sqlite.zst        metadata: fingerprint, sha256, built_at, embed_model
          └─ noncurrent generations ×5, deleted 30 days after replacement
```

Why not one directory per fingerprint plus a `latest.json`: an `age` lifecycle rule would delete
the current index after 30 days without a rebuild. A single object with noncurrent-version rules
cannot lose the live copy.

## Resources

| Resource | Besom type | Purpose |
|---|---|---|
| APIs | `gcp.projects.Service` ×5 | storage, iam, iamcredentials, sts, billingbudgets |
| Bucket | `gcp.storage.Bucket` | `US-CENTRAL1` (free tier region), uniform access, public access prevention enforced, versioning, 2 lifecycle rules |
| Publisher | `gcp.serviceaccount.Account` | the identity CI acts as |
| Bucket write | `gcp.storage.BucketIAMMember` | `roles/storage.objectAdmin` for the publisher, this bucket only |
| Bucket read | `gcp.storage.BucketIAMMember` | `roles/storage.objectViewer` for `reader` (a group, or #398's Cloud Run SA) |
| OIDC pool + provider | `gcp.iam.WorkloadIdentityPool`, `…PoolProvider` | GitHub Actions tokens, only `marola-dev/marola` on `main` |
| Impersonation | `gcp.serviceaccount.IAMMember` | `roles/iam.workloadIdentityUser` for that principal set |
| Budget | `gcp.billing.Budget` | USD 1 with 50 % and 100 % alerts |

## Program (sketch: not compiled; T026 compiles it with `scala-cli compile`)

`infra/org-index/Pulumi.yaml`:

```yaml
name: marola-org-index
runtime: scala
description: GCS hosting for the org knowledge index (marola-devkit specs/001)
```

`infra/org-index/project.scala`:

```scala
//> using scala 3.3.6
//> using dep "org.virtuslab::besom-core:0.5.2"
//> using dep "org.virtuslab::besom-gcp:9.0.0-core.0.5"
import besom.*
import besom.api.gcp
import besom.api.gcp.storage.inputs.*
import besom.api.gcp.billing.inputs.*
import besom.api.gcp.iam.inputs.WorkloadIdentityPoolProviderOidcArgs

@main def main = Pulumi.run {
  val ghRepo = "marola-dev/marola"

  val apis = List(
    "storage.googleapis.com", "iam.googleapis.com", "iamcredentials.googleapis.com",
    "sts.googleapis.com", "billingbudgets.googleapis.com"
  ).map(s => gcp.projects.Service(s, gcp.projects.ServiceArgs(service = s, disableOnDestroy = false)))

  val bucket = gcp.storage.Bucket("org-index", gcp.storage.BucketArgs(
    location = "US-CENTRAL1",
    storageClass = "STANDARD",
    uniformBucketLevelAccess = true,
    publicAccessPrevention = "enforced",
    versioning = BucketVersioningArgs(enabled = true),
    lifecycleRules = List(
      BucketLifecycleRuleArgs(
        action = BucketLifecycleRuleActionArgs(`type` = "Delete"),
        condition = BucketLifecycleRuleConditionArgs(numNewerVersions = 5, withState = "ARCHIVED")),
      BucketLifecycleRuleArgs(
        action = BucketLifecycleRuleActionArgs(`type` = "Delete"),
        condition = BucketLifecycleRuleConditionArgs(daysSinceNoncurrentTime = 30, withState = "ARCHIVED"))
    )
  ), opts(dependsOn = apis))

  val publisher = gcp.serviceaccount.Account("org-index-ci", gcp.serviceaccount.AccountArgs(
    accountId = "org-index-ci",
    displayName = s"org-index publisher ($ghRepo GitHub Actions)"))

  val write = gcp.storage.BucketIAMMember("publisher-writes", gcp.storage.BucketIAMMemberArgs(
    bucket = bucket.name,
    role = "roles/storage.objectAdmin",
    member = publisher.email.map(e => s"serviceAccount:$e")))

  // e.g. `pulumi config set reader group:marola-maintainers@googlegroups.com`
  val read = gcp.storage.BucketIAMMember("reader-reads", gcp.storage.BucketIAMMemberArgs(
    bucket = bucket.name,
    role = "roles/storage.objectViewer",
    member = config.requireString("reader")))

  val pool = gcp.iam.WorkloadIdentityPool("github", gcp.iam.WorkloadIdentityPoolArgs(
    workloadIdentityPoolId = "github",
    displayName = "GitHub Actions"), opts(dependsOn = apis))

  val provider = gcp.iam.WorkloadIdentityPoolProvider("github", gcp.iam.WorkloadIdentityPoolProviderArgs(
    workloadIdentityPoolId = pool.workloadIdentityPoolId,
    workloadIdentityPoolProviderId = "github",
    attributeMapping = Map(
      "google.subject" -> "assertion.sub",
      "attribute.repository" -> "assertion.repository",
      "attribute.ref" -> "assertion.ref"),
    // Without a condition any GitHub repo could mint tokens against this pool.
    attributeCondition = s"assertion.repository == '$ghRepo' && assertion.ref == 'refs/heads/main'",
    oidc = WorkloadIdentityPoolProviderOidcArgs(issuerUri = "https://token.actions.githubusercontent.com")))

  val impersonate = gcp.serviceaccount.IAMMember("ci-impersonates", gcp.serviceaccount.IAMMemberArgs(
    serviceAccountId = publisher.name,
    role = "roles/iam.workloadIdentityUser",
    member = pool.name.map(p => s"principalSet://iam.googleapis.com/$p/attribute.repository/$ghRepo")))

  val budget = gcp.billing.Budget("org-index", gcp.billing.BudgetArgs(
    billingAccount = config.requireString("billingAccount"),
    displayName = "marola org-index",
    amount = BudgetAmountArgs(specifiedAmount = BudgetAmountSpecifiedAmountArgs(currencyCode = "USD", units = "1")),
    thresholdRules = List(BudgetThresholdRuleArgs(thresholdPercent = 0.5), BudgetThresholdRuleArgs(thresholdPercent = 1.0))
  ), opts(dependsOn = apis))

  Stack(write, read, impersonate, budget).exports(
    bucket = bucket.name,
    workloadIdentityProvider = provider.name,
    publisherEmail = publisher.email)
}
```

## Commands

```bash
nix shell nixpkgs#pulumi nixpkgs#scala-cli nixpkgs#google-cloud-sdk   # pulumi 3.263.0 pinned
pulumi plugin install language scala 0.5.2 --server github://api.github.com/VirtusLab/besom
gcloud auth application-default login

# Once, by a human: the state bucket cannot be declared in the stack it holds.
gcloud storage buckets create gs://marola-pulumi-state --location=us-central1 \
  --uniform-bucket-level-access --public-access-prevention
pulumi login gs://marola-pulumi-state

cd infra/org-index
pulumi stack init prod
pulumi config set gcp:project <project-id>
pulumi config set reader group:<maintainers-group>
pulumi config set billingAccount <billing-account-id>   # an id, not a secret
pulumi preview                                           # free; CI may run this
pulumi up                                                # human only, after the go-ahead
```

## Publishing from the umbrella's workflow

The stack outputs become repository variables, not secrets: they are identifiers.

```yaml
permissions: { contents: write, id-token: write }
steps:
  - uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
      service_account: ${{ vars.GCP_ORG_INDEX_SA }}
  - uses: google-github-actions/setup-gcloud@v2
  - run: org-index publish --to gcs "$ORG_INDEX_DB"
    env: { ORG_INDEX_BUCKET: "${{ vars.ORG_INDEX_BUCKET }}" }
```

Reading: `org-index fetch --from gcs`, or by hand
`gcloud storage cp gs://$ORG_INDEX_BUCKET/org-index.sqlite.zst - | zstd -d > org-index.sqlite`.

## Cost

| Item | Usage | Always Free allowance | Expected |
|---|---|---|---|
| Standard storage, us-central1 | ≤ 6 generations × ~25 MB ≈ 150 MB | 5 GB-month | $0 |
| Class A (writes) | ~30/month | 5,000/month | $0 |
| Class B (reads) | hundreds/month | 50,000/month | $0 |
| Egress | ~25 MB per fetch | 100 GB/month from North America | $0 |
| WIF, service account, IAM, budget | — | no charge | $0 |

Past the free tier: $0.02/GB-month Standard storage, so the whole index costs ~$0.003/month. Recheck
on the pricing page at `preview` time; prices move.
