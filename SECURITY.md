# Security policy

## Reporting a problem

Please report security issues privately through GitHub:
**Security** tab of this repository, then **Report a vulnerability**. Do not open a public
issue.

Include the version, OS, what an attacker could do and the steps to reproduce. Use generated
or sample paths, not real ones. You should get a reply within 7 days.

## Supported versions

Only the latest release gets fixes.

## Scope

Things we treat as security issues, for example:

- the app changing or deleting files other than through a confirmed Move to Trash
- following a symlink or junction, or escaping the selected location, in a way that leads to
  trashing the wrong file
- the UI page or a file name being able to run code or call the OS outside the `api/` methods
- any network access, or real paths leaking into logs without home-folder redaction

Release builds are not code signed yet. Download only from this repository's Releases page and
check `SHA256SUMS`.
