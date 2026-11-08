# Security boundaries

This repository processes public report text and model/tool outputs as untrusted
data. It is an application prototype, not a sandbox for arbitrary code execution
or a hosted service with authentication, tenant isolation or production guarantees.

## Implemented controls

- Models receive narrow typed tool interfaces, not raw SQL or Python execution.
  SQL values are parameterized and tool inputs are validated locally.
- The arithmetic calculator uses an AST allowlist and expression, depth, node,
  exponent and magnitude limits. Model/tool/context/result budgets limit execution.
- Prompts declare sources untrusted. Instruction-like retrieved passages and fact
  quotations are screened, and repeated prompt text can be flagged in outputs.
- Citation IDs, quotations and selected numeric associations are checked. These
  diagnostics are not proof of semantic truth or automatic suppression of all bad answers.
- Offline tests use fake clients and block sockets. CI has no model API secrets.
  Python package pins and action commit pins reduce accidental version drift.

## Remaining risks

Pattern screening can miss paraphrased instructions and false numerical data.
Poisoned extraction input, incorrect table associations, unresolved units/scope and
incorrect arithmetic operands remain possible. Historical attack tests cover only
30 cases, and the agent saw planted passages in only 3 of 8 planted cases. These
results do not establish broad resistance to prompt injection.

PDF parsing and downloading depend on third-party code and remote publisher files.
Downloads are not content-hash pinned or comprehensively validated. Keep dependencies
reviewed and do not expose ingestion as a public upload endpoint without additional
isolation, resource controls and file validation. Ingestion is destructive to an
existing local database; preserve consistent backups.

Model requests can send report passages, fact/tool context and questions to the
provider. Logs contain questions, answers and tool inputs. Do not use private data
without reviewing provider/data-handling requirements, and keep generated runs local
unless their contents have been reviewed. `.env` is ignored; environment variables
are preferable for CI/service credentials. Never include keys in issues or artifacts.

## Dependency review and reporting

Dependabot configuration proposes monthly Python/action updates; it does not prove
that all dependencies are vulnerability-free. `pip check` checks compatibility only.
No dedicated vulnerability scanner or automated secret scanner is claimed in this
phase. Review GitHub security advisories and proposed updates; additional scans can
be introduced separately with their own validation and maintenance plan.

For sensitive vulnerability reports, use GitHub private vulnerability reporting if
available, or a private contact method published by the repository owner. Do not
post secrets or exploitable private information in a public issue. Public issues
are appropriate for non-sensitive correctness and documentation problems.
