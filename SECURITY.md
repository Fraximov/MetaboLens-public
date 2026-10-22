# Security and release hygiene

Use the loopback-only local configuration with unique generated credentials.
This alpha has not received an independent penetration test. A shared or
internet-facing deployment also needs HTTPS, an appropriately restricted host,
backups, monitoring and site-specific access review. The workflow worker uses
the Docker socket and must run on a host dedicated to trusted scientific work.

Never disclose credentials in issues or logs. Report security issues privately
to the repository maintainer, using private vulnerability reporting when the
public repository is configured.

SIRIUS login belongs under ignored `runtime/sirius-home/`, with directory mode
0700. Use your own account. Do not copy a logged-in home, `.rtoken`, `.sirius-*`
directory, generated project, or authenticated image into a release. Never run
`docker commit` on a container used for login.

Before distributing a clean source export, run `python3 scripts/check_release.py`
and inspect the exact file list. The scanner rejects known credential paths,
common credential patterns, raw measurements and suspicious archive contents;
it does not guarantee recognition of every possible secret format. It intentionally
rejects an active installation's private `.env` and runtime state. Scan a separate
source export when releasing after local use.
