# Owner questions

## #20: Actions artifact storage access

Git fetch/push and GitHub issue/job/artifact listing APIs work. `gh run download 39110102198 -n parity-profile -n parity-generate-1 -D /tmp/public-b-nightly` fails at the redirected Actions results Azure blob host with Forbidden. An independent urllib request through the inherited proxy reports `Tunnel connection failed: 403 Forbidden`. No artifact contents were obtained. The supported environment status reports no diagnostic reason and network readiness `unknown`. No network-policy bypass attempted.

Options: restore access to the redirected Actions artifact storage host using the supported environment configuration; or provide the two artifact archives through an accessible approved location. Recommendation: restore normal Actions artifact download access, then inspect both archives before any fix or baseline decision. Exact failing workload/verifier/values and root cause remain unknown. No tolerance/pass-rule/recording changes are justified by current evidence.
